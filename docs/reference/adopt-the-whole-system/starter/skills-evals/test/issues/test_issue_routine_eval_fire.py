#!/usr/bin/env python3
"""Issue #71 / ADR 0010: the dispatch-only workflow that fires the eval routine.

`.github/workflows/routine-eval-fire.yml` fires
https://claude.ai/code/routines/trig_EXAMPLE through the
routines fire API. What is pinned here:

  * it triggers on `workflow_dispatch` only (no schedule: #71's gate; no
    pull_request: it would have to be a required check), with exactly the
    inputs mode, candidate, fixture, skill, holdout, section, model, arms and
    trials
    (`model`: test_issue_68_model_input.py), and `permissions: contents: read`
    plus `actions: read` for the weekly allowance guard (#68; the guard
    itself is test_issue_68_allowance_guard.py's);
  * every `uses:` is a 40-character SHA with nothing after it on the line,
    at the same SHA this repo's other workflows already pin;
  * no `${{ inputs.* }}` or `${{ github.event.* }}` inside any `run:` block;
  * `secrets.EVAL_ROUTINE_FIRE_BEARER` appears only as a step `env` value,
    on the fire step, which runs after the validation step;
  * the validation step, run for real, accepts a committed fixture and
    refuses `..`, paths outside evals/, uncommitted fixtures and bad arms or
    trials without echoing the value, and emits a run id of the agreed shape;
  * scaffold mode (the owner, 2026-10-06: "New fire-workflow input
    (Recommended)"): `candidate` must match the miner key pattern in
    scaffold mode and be empty in eval mode, and `fixture` must be empty in
    scaffold mode; both are checked in the same validation step, before the
    bearer is in any step's env;
  * improve mode (ADR 0005, "Routine improve mode addendum"): `skill` must
    match the skill-name pattern and pass `improve_gate.py fire-check` (the
    loop's own resolution: nested `evals/<skill>/<name>` fixtures that all
    name the skill and agentskills, at least `MIN_FIXTURES` of them),
    so `guidance`, `real-work` and other non-skill dirs are refused with
    their own message; `holdout` must be empty or one of those names; both
    must be empty in the other modes, and `fixture` and `candidate` must be
    empty in improve mode;
  * scaffold mode's issue snapshot (the owner, 2026-10-06: "Fire workflow
    precomputes (Recommended)"): a step between validation and the fire step,
    holding only the read-only `github.token`, runs
    `scaffold_real_work.py snapshot` (GraphQL works on Actions, not in the
    routine), and the payload carries its JSON as `issue_snapshot` (null when
    the pull request closes no issue), in scaffold mode only; a payload whose
    `text` is over the fire API's documented 65,536-character cap is refused
    before the API is called, never truncated;
  * the fire step, run against a fake curl, sends the bearer on stdin (never
    argv) and a jq-built payload: eval mode's four fields plus `mode`, or
    exactly `mode`, `run_id`, `candidate` and `issue_snapshot` in scaffold
    mode, or exactly
    `run_id`, `mode`, `skill`, `trials` and `holdout` (null when empty) in
    improve mode, or `run_id`, `mode`, `section`, `trials` and `holdout` in
    guidance-improve mode; it never prints the response body, on success or
    failure;
  * guidance-improve mode (ADR 0005, "Routine guidance improve mode
    addendum"): `section` must match `^[a-z0-9][a-z0-9-]{0,63}$` (so no
    leading `_`) and pass `guidance_improve_gate.py fire-check` (at least the
    proposer's MIN_FIXTURES full-arm fixtures; `holdout` empty or one of
    their keys), and must be empty in every other mode.

The workflow is parsed with PyYAML, never scanned line by line. Discovered
and run by test/run_tests.py; also runnable on its own with
`python3 test/issues/test_issue_routine_eval_fire.py`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

TEST_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = TEST_DIR.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
WORKFLOW = WORKFLOWS / "routine-eval-fire.yml"
sys.path.insert(0, str(TEST_DIR))
from lockfile_pins import lock_problem  # noqa: E402
import example_evals  # noqa: E402

SECRET_REF = "secrets.EVAL_ROUTINE_FIRE_BEARER"
RUN_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}$")
FIXTURE = "evals/writing-adrs/bootstrap"
#: A committed fixture with no top-level `context` mapping (ADR 0012).
NOT_IN_PLACE = "evals/writing-adrs/supersede"
CANDIDATE = "example-adopter___agent-guidance__136"
# A skill with at least three committed fixtures, and one of them.
SKILL = "writing-adrs"
HOLDOUT = "supersede"
PROPOSAL = "proposals/2026-10-26/example-skill__new-skill.json"
# The committed guidance section with the proposer's minimum of full-arm
# fixtures, and one of them as a holdout key.
SECTION = "anything-you-name-gets-its-link"
GHOLDOUT = "guidance/anything-you-name-gets-its-link/handoff"
SKILL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
# The miner key pattern the routine re-checks (routine-delta.md, Payload).
CANDIDATE_RE = re.compile(r"^[A-Za-z0-9-]+__[A-Za-z0-9._-]+__[1-9][0-9]{0,6}$")
# A lexical check on the expression token only, applied to a `run:` value
# the parser already isolated.
FORBIDDEN_IN_RUN = re.compile(r"\$\{\{[^}]*\b(?:inputs\.|github\.event\.)")


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def triggers(doc: dict):
    # A bare `on:` key is the YAML 1.1 boolean True once parsed.
    return doc.get("on", doc.get(True))


def scalars(node, path=()):
    """Every string scalar in a parsed document, with its key path."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from scalars(value, path + (key,))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from scalars(value, path + (index,))
    elif isinstance(node, str):
        yield path, node


def uses_nodes(node):
    """Every `uses:` value node in a composed YAML tree, with its marks."""
    if isinstance(node, yaml.MappingNode):
        for key, value in node.value:
            if isinstance(key, yaml.ScalarNode) and key.value == "uses":
                yield value
            yield from uses_nodes(value)
    elif isinstance(node, yaml.SequenceNode):
        for item in node.value:
            yield from uses_nodes(item)


class WorkflowShapeTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.doc = yaml.safe_load(cls.text)
        cls.job = cls.doc["jobs"]["fire"]
        cls.steps = cls.job["steps"]

    def test_the_job_reads_the_routine_fire_environment(self):
        # C7: the bearer and ROUTINE_TRIGGER_ID live in this environment.
        self.assertEqual(self.job["environment"], "routine-fire")

    def test_only_workflow_dispatch(self):
        on = triggers(self.doc)
        self.assertIsInstance(on, dict)
        self.assertEqual(set(on), {"workflow_dispatch"},
                         "dispatch only: no schedule (#71's gate), no "
                         "pull_request or push (it would need a required check)")
        inputs = on["workflow_dispatch"]["inputs"]
        self.assertEqual(set(inputs),
                         {"mode", "candidate", "fixture", "skill", "holdout",
                          "section", "model", "proposal", "hook_declined", "arms",
                          "trials"})
        self.assertEqual(inputs["mode"]["type"], "choice")
        self.assertEqual(inputs["mode"]["options"],
                         ["eval", "scaffold", "improve", "guidance-improve", "draft"])
        # Draft mode only: the proposal path, and whether the agent-checks
        # lane declined the subject's hook proposal (a choice, so a string
        # in the event file like every other input).
        self.assertEqual(inputs["proposal"]["type"], "string")
        self.assertIs(inputs["proposal"]["required"], False)
        self.assertNotIn("default", inputs["proposal"])
        self.assertEqual(inputs["hook_declined"]["type"], "choice")
        self.assertEqual(inputs["hook_declined"]["options"], ["false", "true"])
        self.assertEqual(inputs["hook_declined"]["default"], "false")
        self.assertEqual(inputs["mode"]["default"], "eval")
        self.assertEqual(inputs["candidate"]["type"], "string")
        # Required in scaffold mode only, so the form cannot require it; the
        # validation step does. Likewise `fixture`, required in eval mode only.
        self.assertIs(inputs["candidate"]["required"], False)
        self.assertIs(inputs["fixture"]["required"], False)
        # Improve mode only (`section` guidance-improve mode only); `holdout`
        # may stay empty even there.
        for name in ("skill", "holdout", "section"):
            self.assertEqual(inputs[name]["type"], "string")
            self.assertIs(inputs[name]["required"], False)
            self.assertNotIn("default", inputs[name])
        self.assertEqual(on["workflow_dispatch"]["inputs"]["arms"]["options"],
                         ["both", "with", "without", "bare"])
        self.assertEqual(on["workflow_dispatch"]["inputs"]["trials"]["options"],
                         ["1", "2", "3"])

    def test_permissions_are_contents_read(self):
        # `actions: read` lets the allowance guard count this workflow's own
        # successful runs (#68); nothing here writes.
        self.assertEqual(self.doc.get("permissions"),
                         {"contents": "read", "actions": "read"})
        for name, job in self.doc["jobs"].items():
            self.assertNotIn("permissions", job,
                             f"job {name} must not widen the workflow's permissions")

    def test_only_the_default_branch_copy_can_fire(self):
        self.assertEqual(
            self.job.get("if"),
            "github.ref == format('refs/heads/{0}', "
            "github.event.repository.default_branch)")

    def test_every_uses_is_locked_and_already_pinned_elsewhere(self):
        pinned_elsewhere = set()
        for other in WORKFLOWS.glob("*.yml"):
            if other == WORKFLOW:
                continue
            for node in uses_nodes(yaml.compose(other.read_text(encoding="utf-8"))):
                pinned_elsewhere.add(node.value)
        lines = self.text.splitlines()
        nodes = list(uses_nodes(yaml.compose(self.text)))
        self.assertTrue(nodes, "expected at least one uses:")
        for node in nodes:
            with self.subTest(uses=node.value):
                self.assertIsNone(lock_problem(node.value, WORKFLOW))
                self.assertEqual(node.start_mark.line, node.end_mark.line)
                rest = lines[node.end_mark.line][node.end_mark.column:]
                self.assertEqual(rest.strip(), "",
                                 "a pin carries no trailing version comment")
                self.assertIn(node.value, pinned_elsewhere,
                              "reuse an action at the tag this repo already pins")

    def test_no_inputs_or_event_expressions_in_run_blocks(self):
        runs = [(p, v) for p, v in scalars(self.doc) if p and p[-1] == "run"]
        self.assertTrue(runs)
        for path, value in runs:
            with self.subTest(path=path):
                self.assertIsNone(FORBIDDEN_IN_RUN.search(value),
                                  "read dispatch inputs from $GITHUB_EVENT_PATH")

    def test_secret_is_referenced_only_in_the_fire_step_env(self):
        hits = [p for p, v in scalars(self.doc) if SECRET_REF in v]
        fire = next(i for i, s in enumerate(self.steps)
                    if s.get("name") == "Fire the eval routine")
        self.assertEqual(hits, [("jobs", "fire", "steps", fire, "env",
                                 "EVAL_ROUTINE_FIRE_BEARER")])
        self.assertNotIn("secrets.", self.steps[fire]["run"])
        # The one other secret is the read-only registry App's key, an input
        # of draft mode's mint step (as propagation.yml mints it).
        mint = next(i for i, s in enumerate(self.steps)
                    if s.get("id") == "lessons-token")
        mentions = [p for p, v in scalars(self.doc) if "secrets." in v]
        self.assertEqual(mentions, [
            ("jobs", "fire", "steps", mint, "with", "private-key"), *hits],
            "no other secret is referenced")

    def test_draft_steps_read_the_lessons_with_a_read_only_token(self):
        ids = [s.get("id") for s in self.steps]
        names = [s.get("name") for s in self.steps]
        fire = names.index("Fire the eval routine")
        mint = ids.index("lessons-token")
        checkout = names.index("Check out the lessons (agentskills-private)")
        build = ids.index("draft-payload")
        self.assertLess(ids.index("validate"), mint)
        self.assertLess(mint, checkout)
        self.assertLess(checkout, build)
        self.assertLess(build, fire)
        for i in (mint, checkout, build):
            self.assertEqual(self.steps[i]["if"],
                             "steps.validate.outputs.mode == 'draft'")
        step = self.steps[mint]
        self.assertTrue(step["uses"].startswith("actions/create-github-app-token@"))
        self.assertEqual(step["with"], {
            "client-id": "${{ vars.REGISTRY_READ_APP_CLIENT_ID }}",
            "private-key": "${{ secrets.REGISTRY_READ_APP_PEM }}",
            "owner": "example-adopter",
            "repositories": "agentskills-private",
            "permission-contents": "read"})
        self.assertEqual(self.steps[checkout]["with"], {
            "repository": "example-adopter/agentskills-private",
            "ref": "persistent/lessons",
            "path": "lessons-repo", "persist-credentials": False,
            "sparse-checkout": "lessons",
            "token": "${{ steps.lessons-token.outputs.token }}"})
        users = [p for p, v in scalars(self.doc) if "steps.lessons-token" in v]
        self.assertEqual(users, [("jobs", "fire", "steps", checkout, "with", "token")])
        build_step = self.steps[build]
        self.assertEqual(build_step["env"], {
            "RUN_ID": "${{ steps.validate.outputs.run_id }}",
            "PROPOSAL": "${{ steps.validate.outputs.proposal }}",
            "HOOK_DECLINED": "${{ steps.validate.outputs.hook_declined }}"})
        self.assertIn("scripts/draft_new_skill.py payload --lessons lessons-repo/lessons",
                      build_step["run"])
        self.assertIn('--proposal="$PROPOSAL"', build_step["run"])
        self.assertIn("scripts/draft_new_skill.py fire-check --payload", build_step["run"])
        self.assertEqual(self.steps[fire]["env"]["DRAFT_PAYLOAD_FILE"],
                         "${{ steps.draft-payload.outputs.file }}")

    def test_validation_step_runs_before_the_fire_step(self):
        ids = [s.get("id") for s in self.steps]
        self.assertIn("validate", ids)
        fire = next(i for i, s in enumerate(self.steps)
                    if s.get("name") == "Fire the eval routine")
        self.assertLess(ids.index("validate"), fire)
        env = self.steps[fire]["env"]
        for key, output in (("RUN_ID", "run_id"), ("FIXTURE", "fixture"),
                            ("ARMS", "arms"), ("TRIALS", "trials"),
                            ("MODE", "mode"), ("CANDIDATE", "candidate"),
                            ("SKILL", "skill"), ("HOLDOUT", "holdout")):
            self.assertEqual(env[key], f"${{{{ steps.validate.outputs.{output} }}}}")
        self.assertEqual(
            env["ROUTINE_TRIGGER_ID"],
            "${{ vars.ROUTINE_TRIGGER_ID }}")
        self.assertNotIn("FIRE_URL", env)

    def test_no_step_before_the_fire_step_sees_the_bearer(self):
        # The validation step (and everything before it) runs with no env
        # that names a secret, and the job sets no env at all: the bearer
        # exists only once every input has passed.
        self.assertNotIn("env", self.job)
        self.assertNotIn("env", self.doc)
        fire = next(i for i, s in enumerate(self.steps)
                    if s.get("name") == "Fire the eval routine")
        for step in self.steps[:fire]:
            with self.subTest(step=step.get("name")):
                text = yaml.safe_dump(step)
                self.assertNotIn("BEARER", text)
                if step.get("id") != "lessons-token":
                    self.assertNotIn("secrets", text)

    def test_the_snapshot_step_sits_between_validation_and_the_fire_step(self):
        ids = [s.get("id") for s in self.steps]
        fire = next(i for i, s in enumerate(self.steps)
                    if s.get("name") == "Fire the eval routine")
        self.assertIn("snapshot", ids)
        snap = ids.index("snapshot")
        self.assertLess(ids.index("validate"), snap)
        self.assertLess(snap, fire)
        step = self.steps[snap]
        self.assertEqual(step["if"], "steps.validate.outputs.mode == 'scaffold'")
        # The read-only job token for the GitHub reads, the validated
        # candidate, and nothing else: never the bearer.
        self.assertEqual(step["env"], {
            "GH_TOKEN": "${{ github.token }}",
            "CANDIDATE": "${{ steps.validate.outputs.candidate }}",
            "FLEET_REGISTRY": "_agent-guidance/repos.yml",
            "FLEET_SYNC_WORKFLOW": "_agent-guidance/.github/workflows/sync.yml"})
        self.assertIn("python3 scripts/scaffold_real_work.py snapshot "
                      '--candidate="$CANDIDATE"', step["run"])
        self.assertIn('--registry "$FLEET_REGISTRY" --sync-workflow "$FLEET_SYNC_WORKFLOW"',
                      step["run"])
        # The fleet pin (the owner, 2026-10-06: "Pin to fleet owners
        # (Recommended)") reads _agent-guidance's default branch, checked
        # out before this step, credentials not kept.
        [ag] = [i for i, s in enumerate(self.steps) if (s.get("with") or {}).get("repository")
                == "example-adopter/_agent-guidance"]
        self.assertLess(ag, snap)
        self.assertTrue(self.steps[ag]["uses"].startswith("actions/checkout@"))
        self.assertEqual(self.steps[ag]["with"], {
            "repository": "example-adopter/_agent-guidance", "path": "_agent-guidance",
            "persist-credentials": False})
        self.assertEqual(self.steps[fire]["env"]["ISSUE_SNAPSHOT_FILE"],
                         "${{ steps.snapshot.outputs.file }}")
        # The job token reaches the snapshot step and the allowance guard
        # (#68), which reads the workflow runs API with it, and no other.
        holders = [s.get("name") for s in self.steps
                   if "github.token" in yaml.safe_dump(s)]
        self.assertEqual(holders, ["Check the weekly allowance", step["name"]])

    def test_pinned_pyyaml_is_installed_before_validation(self):
        # improve_gate.py fire-check loads fixture.yaml with PyYAML, at the
        # pin the gate workflows install; no secret is in this step's reach.
        ids = [s.get("id") for s in self.steps]
        installs = [i for i, s in enumerate(self.steps)
                    if s.get("run") == "python3 -m pip install pyyaml==6.0.3"]
        self.assertEqual(len(installs), 1)
        self.assertLess(installs[0], ids.index("validate"))
        self.assertNotIn("env", self.steps[installs[0]])

    def test_checkout_does_not_persist_credentials(self):
        checkout = [s for s in self.steps
                    if str(s.get("uses", "")).startswith("actions/checkout@")]
        # The default branch, persistent/eval-results for the allowance
        # guard (#68), _agent-guidance for scaffold mode and the lessons for
        # draft mode.
        self.assertEqual(len(checkout), 4)
        for step in checkout:
            self.assertIs(step["with"]["persist-credentials"], False)


def step_script(name: str) -> str:
    steps = load(WORKFLOW)["jobs"]["fire"]["steps"]
    return next(s for s in steps if s.get("name") == name)["run"]


@unittest.skipUnless(shutil.which("jq") and shutil.which("git"),
                     "needs jq and git on PATH")
class ValidateStepTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.script = step_script("Validate the dispatch inputs")

    def run_step(self, inputs):
        event = self.tmp / "event.json"
        event.write_text(json.dumps({"inputs": inputs}), encoding="utf-8")
        output = self.tmp / "output"
        output.write_text("", encoding="utf-8")
        # A UTF-8 locale, as on an ubuntu runner: the step's own
        # `export LC_ALL=C` is then what keeps [A-Za-z] to ASCII.
        env = {"PATH": os.environ["PATH"], "HOME": str(self.tmp),
               "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
               "GITHUB_EVENT_PATH": str(event), "GITHUB_OUTPUT": str(output)}
        # The step reads the checkout's committed fixtures: the scratch
        # checkout carries the example ones (test/example_evals.py).
        proc = subprocess.run(["bash", "-c", self.script], cwd=example_evals.checkout(),
                              env=env, capture_output=True, text=True,
                              timeout=60)
        values = dict(line.split("=", 1) for line in
                      output.read_text(encoding="utf-8").splitlines())
        return proc, values

    def test_accepts_a_committed_fixture(self):
        proc, values = self.run_step(
            {"mode": "eval", "candidate": "", "fixture": FIXTURE,
             "arms": "with", "trials": "3"})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(values["mode"], "eval")
        self.assertEqual(values["candidate"], "")
        self.assertEqual(values["fixture"], FIXTURE)
        self.assertEqual((values["skill"], values["holdout"]), ("", ""))
        self.assertEqual((values["arms"], values["trials"]), ("with", "3"))
        self.assertRegex(values["run_id"], RUN_ID)

    def test_an_absent_candidate_reads_as_empty_in_eval_mode(self):
        # A string input with no default may be left out of the event.
        proc, values = self.run_step(
            {"mode": "eval", "fixture": FIXTURE, "arms": "both", "trials": "1"})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(values["candidate"], "")

    def test_accepts_a_scaffold_candidate(self):
        for candidate in (CANDIDATE, "example-adopter-two__example-site-b__1",
                          "a-b__c.d-e_f__9999999"):
            with self.subTest(candidate=candidate):
                self.assertRegex(candidate, CANDIDATE_RE)
                proc, values = self.run_step(
                    {"mode": "scaffold", "candidate": candidate, "fixture": "",
                     "arms": "both", "trials": "1"})
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertEqual(values["mode"], "scaffold")
                self.assertEqual(values["candidate"], candidate)
                self.assertEqual(values["fixture"], "")
                self.assertRegex(values["run_id"], RUN_ID)

    def test_refuses_bad_scaffold_inputs_without_echoing_them(self):
        good = {"mode": "scaffold", "candidate": CANDIDATE, "fixture": "",
                "arms": "both", "trials": "1"}
        cases = {
            "missing_candidate": {"candidate": None},
            "empty_candidate": {"candidate": ""},
            "fixture_given": {"fixture": FIXTURE},
            "single_underscores": {"candidate": "example-adopter_repo_12"},
            "underscore_in_owner": {"candidate": "Own_S__repo__12"},
            "underscore_after_number": {"candidate": "example-adopter__repo__12_"},
            "empty_owner": {"candidate": "__repo__12"},
            "empty_repo": {"candidate": "example-adopter____12"},
            "two_keys": {"candidate": "example-adopter__repo__12 example-adopter__repo__13"},
            "zero_pr": {"candidate": "example-adopter__repo__0"},
            "leading_zero": {"candidate": "example-adopter__repo__012"},
            "eight_digits": {"candidate": "example-adopter__repo__12345678"},
            "no_number": {"candidate": "example-adopter__repo"},
            "shell_subst": {"candidate": "example-adopter__$(id)__12"},
            "shell_semicolon": {"candidate": "example-adopter__repo__12;id"},
            "shell_backtick": {"candidate": "example-adopter__`id`__12"},
            "shell_pipe": {"candidate": "example-adopter__repo|id__12"},
            "slash": {"candidate": "example-adopter__../repo__12"},
            "quote": {"candidate": 'example-adopter__re"po__12'},
            "space": {"candidate": "example-adopter__re po__12"},
            "newline_inside": {"candidate": "example-adopter__repo__12\nx__y__1"},
            "trailing_newline": {"candidate": "example-adopter__repo__12\n"},
            "nul": {"candidate": "example-adopter__repo__12\u0000"},
            "non_ascii": {"candidate": "example-adopter__r\u00e9po__12"},
            "fullwidth": {"candidate": "example-adopter__r\uff41po__12"},
            "nbsp": {"candidate": "example-adopter__re\u00a0po__12"},
            "line_separator": {"candidate": "example-adopter__re\u2028po__12"},
            "candidate_number": {"candidate": 12},
            "mode_unknown": {"mode": "improved"},
            "mode_case_improve": {"mode": "Improve"},
            "mode_case": {"mode": "Scaffold"},
            "mode_newline": {"mode": "scaffold\n"},
            "mode_missing": {"mode": None},
            "mode_number": {"mode": 1},
        }
        self._refuse_all(good, cases)

    def test_refuses_a_candidate_in_eval_mode(self):
        good = {"mode": "eval", "candidate": "", "fixture": FIXTURE,
                "arms": "both", "trials": "1"}
        self._refuse_all(good, {
            "candidate_in_eval": {"candidate": CANDIDATE},
            "whitespace_candidate": {"candidate": " "},
            "missing_fixture": {"fixture": None},
        })

    def test_accepts_a_holdout_of_exactly_128_characters(self):
        # Isolate the dispatch shape boundary from fixture membership, which
        # fire-check tests separately against the committed fixture names.
        holdout = "h" * 128
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        checker = bin_dir / "python3"
        checker.write_text(f"#!{sys.executable}\nimport sys\n"
                           f"assert sys.argv[1:] == "
                           f"{['scripts/improve_gate.py', 'fire-check', SKILL, '--holdout=' + holdout]!r}\n")
        checker.chmod(0o755)
        with mock.patch.dict(os.environ, {"PATH": f"{bin_dir}:{os.environ['PATH']}"}):
            proc, values = self.run_step(
                {"mode": "improve", "candidate": "", "fixture": "", "skill": SKILL,
                 "holdout": holdout, "arms": "both", "trials": "1"})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(values["holdout"], holdout)

    def test_accepts_an_improve_run(self):
        # writing-adrs: four nested agentskills fixtures.
        cases = (
            ({"skill": SKILL, "holdout": HOLDOUT}, SKILL, HOLDOUT),
            ({"skill": SKILL, "holdout": ""}, SKILL, ""),
            ({"skill": SKILL}, SKILL, ""),
            ({"skill": SKILL, "holdout": "bootstrap"}, SKILL, "bootstrap"),
        )
        for extra, skill, holdout in cases:
            with self.subTest(**extra):
                self.assertRegex(skill, SKILL_RE)
                proc, values = self.run_step(
                    {"mode": "improve", "candidate": "", "fixture": "",
                     "arms": "both", "trials": "2", **extra})
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertEqual(values["mode"], "improve")
                self.assertEqual(values["skill"], skill)
                self.assertEqual(values["holdout"], holdout)
                self.assertEqual(values["trials"], "2")
                self.assertEqual((values["fixture"], values["candidate"]), ("", ""))
                self.assertRegex(values["run_id"], RUN_ID)

    def test_refuses_bad_improve_inputs_without_echoing_them(self):
        good = {"mode": "improve", "candidate": "", "fixture": "",
                "skill": SKILL, "holdout": HOLDOUT, "arms": "both",
                "trials": "1"}
        # Each refusal is pinned to its own check by its message, with an
        # empty holdout, so a later check cannot mask a missing earlier one.
        no_holdout = {**good, "holdout": ""}
        self._refuse_all(no_holdout, {
            "missing_skill": {"skill": None},
            "empty_skill": {"skill": ""},
            "whitespace_skill": {"skill": " "},
            "skill_is_a_fixture_path": {"skill": "writing-adrs/bootstrap"},
            "skill_with_evals": {"skill": "evals/writing-adrs"},
            "skill_dotdot": {"skill": "../writing-adrs"},
            "skill_leading_dash": {"skill": "-writing-adrs"},
            "skill_leading_dot": {"skill": ".writing-adrs"},
            "skill_too_long": {"skill": "w" * 65},
            "skill_glob": {"skill": "writing-adr*"},
            "skill_subst": {"skill": "$(id)"},
            "skill_space": {"skill": "writing adrs"},
            "skill_non_ascii": {"skill": "writing-adr\u00e9"},
            "skill_fullwidth": {"skill": "writing-adr\uff41"},
            "skill_nbsp": {"skill": "writing\u00a0adrs"},
            "skill_line_separator": {"skill": "writing\u2028adrs"},
        }, expect="is not a skill name")
        # The step's own holdout shape check, ahead of fire-check, which
        # would refuse these too but with another message.
        self._refuse_all(good, {
            "holdout_too_long": {"holdout": "h" * 129},
            "holdout_space": {"holdout": "super sede"},
            "holdout_slash": {"holdout": "supersede/x"},
            "holdout_subst": {"holdout": "$(id)"},
            "holdout_non_ascii": {"holdout": "supersed\u00e9"},
            "holdout_fullwidth": {"holdout": "supersed\uff45"},
            "holdout_nbsp": {"holdout": "super\u00a0sede"},
            "holdout_line_separator": {"holdout": "super\u2028sede"},
        }, expect="is longer than 128 characters or has characters outside")
        # An option-shaped holdout is one `--holdout=VALUE` word, so the
        # checker reads it as a name (no such fixture), never as a flag.
        self._refuse_all(good, {
            "holdout_short_help": {"holdout": "-h"},
            "holdout_long_help": {"holdout": "--help"},
            "holdout_help_prefix": {"holdout": "--he"},
            "holdout_own_flag": {"holdout": "--holdout"},
            "holdout_unknown_flag": {"holdout": "-x"},
            "holdout_double_dash": {"holdout": "--"},
        }, expect="is refused by the improvement loop's fixture check",
            forbid="usage")
        # The loop's own resolution (improve_gate.py fire-check): not a
        # skill's fixture set, another registry's skill, too few fixtures.
        self._refuse_all(no_holdout, {
            "unknown_skill": {"skill": "no-such-skill-xyz"},
            "skill_prefix_only": {"skill": "writing-adr"},
            "flat_fixture_skill": {"skill": "browser-testing"},
            "guidance": {"skill": "guidance"},
            "real_work": {"skill": "real-work"},
        }, expect="is not one skill's fixture set")
        self._refuse_all(no_holdout, {
            # Three nested fixtures, but for agentskills-private.
            "private_registry_skill": {"skill": "my-writing-style"},
        }, expect="is not an agentskills skill")
        self._refuse_all(no_holdout, {
            # Two nested fixtures.
            "two_fixture_skill": {"skill": "github-actions-repo-settings"},
        }, expect="fewer than 3 nested fixtures")
        self._refuse_all(good, {
            "holdout_not_a_fixture": {"holdout": "no-such-holdout-xyz"},
            "holdout_of_another_skill": {"holdout": "proposal-bio"},
            "holdout_dotdot": {"holdout": ".."},
        }, expect="names no nested fixture of that skill")
        self._refuse_all(good, {
            "skill_trailing_newline": {"skill": SKILL + "\n"},
            "skill_tab": {"skill": SKILL + "\t"},
            "skill_del": {"skill": SKILL + "\u007f"},
            "skill_nul": {"skill": SKILL + "\u0000"},
            "skill_number": {"skill": 7},
            "holdout_full_path": {"holdout": FIXTURE},
            "holdout_nested": {"holdout": "supersede/x"},
            "holdout_whitespace": {"holdout": " "},
            "holdout_trailing_newline": {"holdout": HOLDOUT + "\n"},
            "holdout_tab": {"holdout": HOLDOUT + "\t"},
            "holdout_nul": {"holdout": HOLDOUT + "\u0000"},
            "holdout_number": {"holdout": 1},
            "fixture_given": {"fixture": FIXTURE},
            "candidate_given": {"candidate": CANDIDATE},
            "trials_high": {"trials": "4"},
        })

    def test_refuses_improve_inputs_in_other_modes(self):
        eval_good = {"mode": "eval", "candidate": "", "fixture": FIXTURE,
                     "arms": "both", "trials": "1"}
        scaffold_good = {"mode": "scaffold", "candidate": CANDIDATE,
                         "fixture": "", "arms": "both", "trials": "1"}
        for good in (eval_good, scaffold_good):
            with self.subTest(mode=good["mode"]):
                self._refuse_all(good, {
                    "skill_given": {"skill": SKILL},
                    "whitespace_skill": {"skill": " "},
                    "holdout_given": {"holdout": HOLDOUT},
                    "skill_number": {"skill": 7},
                    "holdout_nul": {"holdout": "\u0000"},
                })

    def test_accepts_a_draft_run(self):
        for extra, declined in (({}, "false"), ({"hook_declined": "false"}, "false"),
                                ({"hook_declined": "true"}, "true")):
            with self.subTest(**extra):
                proc, values = self.run_step(
                    {"mode": "draft", "proposal": PROPOSAL, "arms": "both",
                     "trials": "1", **extra})
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertEqual(values["mode"], "draft")
                self.assertEqual(values["proposal"], PROPOSAL)
                self.assertEqual(values["hook_declined"], declined)
                for key in ("fixture", "candidate", "skill", "holdout", "model"):
                    self.assertEqual(values[key], "")
                self.assertRegex(values["run_id"], RUN_ID)

    def test_refuses_bad_draft_inputs_without_echoing_them(self):
        good = {"mode": "draft", "proposal": PROPOSAL, "arms": "both",
                "trials": "1"}
        self._refuse_all(good, {
            "missing_proposal": {"proposal": None},
            "empty_proposal": {"proposal": ""},
            "not_new_skill": {"proposal": "proposals/2026-10-26/example-skill__skill-edit.json"},
            "no_date_dir": {"proposal": "example-skill__new-skill.json"},
            "lessons_prefix": {"proposal": "lessons/" + PROPOSAL},
            "dotdot": {"proposal": "proposals/2026-10-26/../x__new-skill.json"},
            "upper_subject": {"proposal": "proposals/2026-10-26/Example__new-skill.json"},
            "leading_dash_subject": {"proposal": "proposals/2026-10-26/-x__new-skill.json"},
            "long_subject": {"proposal": f"proposals/2026-10-26/{'s' * 65}__new-skill.json"},
            "bad_date": {"proposal": "proposals/2026-1-26/example-skill__new-skill.json"},
            "nested": {"proposal": "proposals/2026-10-26/x/example-skill__new-skill.json"},
            "subst": {"proposal": "proposals/2026-10-26/$(id)__new-skill.json"},
            "non_ascii": {"proposal": "proposals/2026-10-26/\u00e9x__new-skill.json"},
        }, expect="is not proposals/<date>/<subject>__new-skill.json")
        self._refuse_all(good, {
            "trailing_newline": {"proposal": PROPOSAL + "\n"},
            "nul": {"proposal": PROPOSAL + "\u0000"},
            "number": {"proposal": 7},
            "fixture_given": {"fixture": FIXTURE},
            "candidate_given": {"candidate": CANDIDATE},
            "skill_given": {"skill": SKILL},
            "holdout_given": {"holdout": HOLDOUT},
            "model_given": {"model": "claude-opus-5-5"},
            "declined_word": {"hook_declined": "yes"},
            "declined_case": {"hook_declined": "True"},
            "declined_bool": {"hook_declined": True},
        })

    def test_refuses_draft_inputs_in_other_modes(self):
        goods = (
            {"mode": "eval", "fixture": FIXTURE, "arms": "both", "trials": "1"},
            {"mode": "scaffold", "candidate": CANDIDATE, "arms": "both", "trials": "1"},
            {"mode": "improve", "skill": SKILL, "arms": "both", "trials": "1"},
        )
        for good in goods:
            with self.subTest(mode=good["mode"]):
                self._refuse_all(good, {
                    "proposal_given": {"proposal": PROPOSAL},
                    "whitespace_proposal": {"proposal": " "},
                }, expect="must be empty and 'hook_declined' false unless the mode is draft")
                self._refuse_all(good, {
                    "declined_given": {"hook_declined": "true"},
                })

    def test_accepts_bare_for_a_fixture_that_runs_in_place(self):
        # ADR 0012's bare baseline addendum: FIXTURE's fixture.yaml has a
        # top-level `context` mapping.
        proc, values = self.run_step(
            {"mode": "eval", "fixture": FIXTURE, "arms": "bare", "trials": "3",
             "model": "claude-sonnet-5-5"})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual((values["arms"], values["trials"], values["model"]),
                         ("bare", "3", "claude-sonnet-5-5"))
        # `model` stays optional, as for any eval fire.
        proc, values = self.run_step(
            {"mode": "eval", "fixture": FIXTURE, "arms": "bare", "trials": "1"})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(values["model"], "")

    def test_refuses_bare_for_a_fixture_that_does_not_run_in_place(self):
        # Committed, but with no `context` mapping: its isolation `without`
        # arm already is its bare arm.
        for fixture in (NOT_IN_PLACE, "evals/guidance/anything-you-name-gets-its-link/handoff"):
            with self.subTest(fixture=fixture):
                proc, values = self.run_step(
                    {"mode": "eval", "fixture": fixture, "arms": "bare", "trials": "3"})
                self.assertNotEqual(proc.returncode, 0)
                self.assertEqual(values, {}, "no output on a refusal")
                self.assertIn("::error::input 'arms' is bare but the fixture does not run "
                              "in place (no top-level context mapping)", proc.stdout)
                self.assertNotIn(fixture, proc.stdout + proc.stderr)
                # The same fixture is fine with both arms.
                proc, _ = self.run_step(
                    {"mode": "eval", "fixture": fixture, "arms": "both", "trials": "1"})
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_refuses_bare_outside_eval_mode(self):
        cases = {"improve": {"mode": "improve", "skill": SKILL, "holdout": HOLDOUT},
                 "scaffold": {"mode": "scaffold", "candidate": CANDIDATE},
                 "draft": {"mode": "draft", "proposal": PROPOSAL}}
        for label, inputs in cases.items():
            with self.subTest(mode=label):
                proc, values = self.run_step({**inputs, "arms": "bare", "trials": "1"})
                self.assertNotEqual(proc.returncode, 0)
                self.assertEqual(values, {})
                self.assertIn("::error::input 'arms' may be bare in eval mode only",
                              proc.stdout)
                # The same inputs with arms both pass this step.
                proc, _ = self.run_step({**inputs, "arms": "both", "trials": "1"})
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_accepts_a_guidance_improve_run(self):
        # The one committed section with the proposer's four full-arm
        # fixtures; holdout is a fixture key relative to evals/.
        for holdout in (GHOLDOUT, ""):
            with self.subTest(holdout=holdout):
                proc, values = self.run_step(
                    {"mode": "guidance-improve", "section": SECTION,
                     "holdout": holdout, "arms": "both", "trials": "3"})
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertEqual(values["mode"], "guidance-improve")
                self.assertEqual(values["section"], SECTION)
                self.assertEqual(values["holdout"], holdout)
                self.assertEqual(values["trials"], "3")
                self.assertEqual((values["fixture"], values["candidate"],
                                  values["skill"]), ("", "", ""))
                self.assertRegex(values["run_id"], RUN_ID)

    def test_refuses_bad_guidance_improve_inputs_without_echoing_them(self):
        good = {"mode": "guidance-improve", "section": SECTION, "holdout": "",
                "arms": "both", "trials": "3"}
        self._refuse_all(good, {
            "missing_section": {"section": None},
            "empty_section": {"section": ""},
            "leading_underscore": {"section": "_delivery"},
            "upper_case": {"section": "Anything-You-Name"},
            "leading_dash": {"section": "-anything"},
            "dot": {"section": "anything.you"},
            "slash": {"section": "guidance/anything"},
            "dotdot": {"section": ".."},
            "too_long": {"section": "a" * 65},
            "subst": {"section": "$(id)"},
            "space": {"section": "anything you"},
            "non_ascii": {"section": "anything-you-nam\u00e9"},
        }, expect="is not a guidance section id")
        self._refuse_all(good, {
            "skill_given": {"skill": SKILL},
        }, expect="'skill' must be empty in guidance-improve mode")
        self._refuse_all(good, {
            "fixture_given": {"fixture": FIXTURE},
            "candidate_given": {"candidate": CANDIDATE},
        })
        self._refuse_all(good, {
            "holdout_bare_name": {"holdout": "handoff"},
            "holdout_with_evals": {"holdout": "evals/" + GHOLDOUT},
            "holdout_dotdot": {"holdout": "guidance/../x"},
            "holdout_subst": {"holdout": "guidance/$(id)"},
        }, expect="is not a guidance fixture key")
        # Well-shaped, but the proposer would refuse: too few full-arm
        # fixtures, or a holdout of another section.
        self._refuse_all(good, {
            "too_few_fixtures": {"section": "two-setup-gaps"},
            "no_such_section": {"section": "no-such-section-xyz"},
            "holdout_of_another_section": {
                "holdout": "guidance/two-setup-gaps/no-such-fixture"},
        }, expect="refused by the guidance proposer's fixture check")

    def test_refuses_a_section_outside_guidance_improve_mode(self):
        cases = (({"mode": "eval", "fixture": FIXTURE}, ),
                 ({"mode": "improve", "skill": SKILL}, ),
                 ({"mode": "scaffold", "candidate": CANDIDATE}, ))
        for (base,) in cases:
            with self.subTest(mode=base["mode"]):
                self._refuse_all({**base, "arms": "both", "trials": "1"},
                                 {"section_given": {"section": SECTION}},
                                 expect="'section' must be empty unless the mode is "
                                        "guidance-improve")

    def _refuse_all(self, good, cases, expect=None, forbid=None):
        for label, override in cases.items():
            with self.subTest(case=label):
                inputs = {**good, **override}
                inputs = {k: v for k, v in inputs.items() if v is not None}
                proc, values = self.run_step(inputs)
                self.assertNotEqual(proc.returncode, 0)
                self.assertEqual(values, {}, "no output on a refusal")
                self.assertIn("::error::", proc.stdout + proc.stderr)
                if expect is not None:
                    self.assertIn(expect, proc.stdout + proc.stderr)
                if forbid is not None:
                    self.assertNotIn(forbid, proc.stdout + proc.stderr)
                bad = next(iter(override.values()))
                if isinstance(bad, str) and bad.strip():
                    self.assertNotIn(bad.strip(), proc.stdout + proc.stderr)

    def test_refuses_bad_inputs_without_echoing_them(self):
        good = {"mode": "eval", "candidate": "", "fixture": FIXTURE,
                "arms": "both", "trials": "1"}
        cases = {
            "dotdot": {"fixture": "evals/../evals/writing-adrs/bootstrap"},
            "dot": {"fixture": "evals/./writing-adrs/bootstrap"},
            "outside_evals": {"fixture": "harness"},
            "uncommitted": {"fixture": "evals/no-such-fixture-xyz"},
            "prefix_only": {"fixture": "evals/writing-adrs"},
            "multiline": {"fixture": FIXTURE + "\nevals/browser-testing"},
            "shell_chars": {"fixture": "evals/$(id)"},
            "nul": {"fixture": FIXTURE + "\u0000"},
            "empty": {"fixture": ""},
            "arms": {"arms": "all-of-them"},
            "trials_high": {"trials": "4"},
            "trials_zero": {"trials": "0"},
            "trials_number": {"trials": 2},
            "missing": {"arms": None},
        }
        for label, override in cases.items():
            with self.subTest(case=label):
                inputs = {**good, **override}
                inputs = {k: v for k, v in inputs.items() if v is not None}
                proc, values = self.run_step(inputs)
                self.assertNotEqual(proc.returncode, 0)
                self.assertNotIn("run_id", values)
                bad = next(iter(override.values()))
                if isinstance(bad, str) and bad:
                    self.assertNotIn(bad, proc.stdout + proc.stderr)


@unittest.skipUnless(shutil.which("git"), "needs git on PATH")
class SnapshotStepTests(unittest.TestCase):
    """The snapshot step, run for real against a fake gh (REST + GraphQL)."""

    def setUp(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import test_issue_real_work_scaffold as rw
        self.rw = rw
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        gh = self.bin / "gh"
        gh.write_text(f"#!{sys.executable}\n" + rw.FAKE_GH, encoding="utf-8")
        gh.chmod(0o755)
        self.data = self.tmp / "gh.json"
        self.registry, self.sync = rw.write_fleet(self.tmp / "_agent-guidance")
        self.script = step_script("Compute the issue snapshot")

    def run_step(self, candidate="example__toy__7", **answers):
        self.data.write_text(json.dumps(answers), encoding="utf-8")
        output = self.tmp / "output"
        output.write_text("", encoding="utf-8")
        runner_temp = self.tmp / "runner"
        shutil.rmtree(runner_temp, ignore_errors=True)
        runner_temp.mkdir()
        env = {"PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
               "HOME": str(self.tmp), "LANG": "C.UTF-8",
               "GH_TOKEN": "fake", "CANDIDATE": candidate,
               "FLEET_REGISTRY": str(self.registry),
               "FLEET_SYNC_WORKFLOW": str(self.sync),
               "RUNNER_TEMP": str(runner_temp), "GITHUB_OUTPUT": str(output),
               "FAKE_GH_LOG": str(self.tmp / "gh.log"),
               "FAKE_GH_DATA": str(self.data)}
        proc = subprocess.run(["bash", "-c", self.script], cwd=REPO_ROOT, env=env,
                              capture_output=True, text=True, timeout=60)
        values = dict(line.split("=", 1) for line in
                      output.read_text(encoding="utf-8").splitlines())
        return proc, values

    def test_writes_the_snapshot_file_and_names_it(self):
        rw = self.rw
        proc, values = self.run_step(**rw.rest(3), graphql=rw.graphql())
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        doc = json.loads(Path(values["file"]).read_text(encoding="utf-8"))
        self.assertEqual(doc, rw.precomputed())
        self.assertNotIn("subtracts", proc.stdout + proc.stderr)

    def test_no_closing_issue_writes_null(self):
        rw = self.rw
        proc, values = self.run_step(**rw.rest(), graphql=4)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIsNone(json.loads(Path(values["file"]).read_text(encoding="utf-8")))

    def test_a_candidate_outside_the_fleet_is_refused_before_any_read(self):
        rw = self.rw
        proc, values = self.run_step(candidate="attacker__toy__7", **rw.rest(3),
                                     graphql=rw.graphql())
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not a fleet owner", proc.stderr)
        self.assertNotIn("file", values)
        self.assertFalse((self.tmp / "gh.log").exists())

    def test_an_option_shaped_candidate_is_read_as_a_key_not_a_flag(self):
        # The miner key pattern admits a leading `-`; `--candidate=VALUE`
        # is one word, so the snapshot refuses the key with its own fixed
        # line instead of argparse printing its usage.
        rw = self.rw
        for candidate in ("-h__toy__7", "--help__toy__7", "--out__toy__7"):
            with self.subTest(candidate=candidate):
                proc, values = self.run_step(candidate=candidate, **rw.rest(3),
                                             graphql=rw.graphql())
                self.assertNotEqual(proc.returncode, 0)
                self.assertEqual(proc.stderr, "scaffold_real_work: the candidate "
                                 "is not a miner key (OWNER__REPO__PR)\n")
                self.assertNotIn("file", values)
                self.assertFalse((self.tmp / "gh.log").exists())

    def test_a_refusal_fails_the_step_and_names_no_file(self):
        rw = self.rw
        proc, values = self.run_step(**rw.rest(3, 4), graphql=4)
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn("file", values)


FAKE_CURL = """#!/usr/bin/env python3
import json, os, sys
log = os.environ["FAKE_CURL_LOG"]
args = sys.argv[1:]
with open(os.path.join(log, "argv.json"), "w") as f:
    json.dump(args, f)
with open(os.path.join(log, "stdin"), "w") as f:
    f.write(sys.stdin.read())
with open(os.path.join(log, "calls"), "a") as f:
    f.write("call\\n")
with open(args[args.index("--output") + 1], "w") as f:
    f.write(os.environ["FAKE_CURL_BODY"])
sys.stdout.write(os.environ["FAKE_CURL_STATUS"])
sys.exit(int(os.environ.get("FAKE_CURL_EXIT", "0")))
"""


# Wraps the real jq: logs each call's argv and stdout, so a test can show
# the body curl sent is byte for byte what a `jq -n` call printed.
FAKE_JQ = """#!/usr/bin/env python3
import json, os, subprocess, sys
proc = subprocess.run([os.environ["REAL_JQ"], *sys.argv[1:]],
                      stdin=sys.stdin, capture_output=True)
with open(os.path.join(os.environ["FAKE_CURL_LOG"], "jq.jsonl"), "a") as f:
    f.write(json.dumps({"argv": sys.argv[1:],
                        "stdout": proc.stdout.decode("utf-8", "replace")}) + "\\n")
sys.stdout.buffer.write(proc.stdout)
sys.stderr.buffer.write(proc.stderr)
sys.exit(proc.returncode)
"""


class FireStepTests(unittest.TestCase):

    BEARER = "sk-ant-oat01-FAKE-BEARER-FOR-TESTS"
    SESSION = "session_01TestOnlyAbc"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        curl = self.bin / "curl"
        curl.write_text(FAKE_CURL, encoding="utf-8")
        curl.chmod(curl.stat().st_mode | stat.S_IXUSR)
        self.log = self.tmp / "log"
        self.log.mkdir()
        self.summary = self.tmp / "summary.md"
        self.snapshot = self.tmp / "issue-snapshot.json"
        self.snapshot.write_text("null\n", encoding="utf-8")
        self.script = step_script("Fire the eval routine")
        self.env_block = next(
            s for s in load(WORKFLOW)["jobs"]["fire"]["steps"]
            if s.get("name") == "Fire the eval routine")["env"]

    def run_step(self, status, body, curl_exit=0, **overrides):
        self.summary.write_text("", encoding="utf-8")
        (self.log / "calls").unlink(missing_ok=True)
        (self.log / "argv.json").unlink(missing_ok=True)
        env = {
            "FAKE_CURL_EXIT": str(curl_exit),
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(self.tmp),
            "ROUTINE_TRIGGER_ID": "trig_EXAMPLE",
            "EVAL_ROUTINE_FIRE_BEARER": self.BEARER,
            "RUN_ID": "20261006T120000Z-a1b2c3",
            "FIXTURE": FIXTURE, "ARMS": "both", "TRIALS": "2",
            "MODE": "eval", "CANDIDATE": "", "SKILL": "", "HOLDOUT": "", "SECTION": "",
            "ISSUE_SNAPSHOT_FILE": str(self.snapshot),
            "GITHUB_STEP_SUMMARY": str(self.summary),
            "FAKE_CURL_LOG": str(self.log),
            "FAKE_CURL_STATUS": status, "FAKE_CURL_BODY": body,
            **overrides,
        }
        return subprocess.run(["bash", "-c", self.script], cwd=self.tmp,
                              env=env, capture_output=True, text=True,
                              timeout=60)

    def ok_body(self, **extra):
        return json.dumps({
            "type": "routine_fire",
            "claude_code_session_id": self.SESSION,
            "claude_code_session_url": f"https://claude.ai/code/{self.SESSION}",
            **extra})

    @unittest.skipUnless(shutil.which("jq"), "needs jq on PATH")
    def test_a_cse_id_with_the_documented_url_relation_is_accepted(self):
        # Observed (re-fire run 37521038032, shape line only): the id is
        # cse_<24 chars> and the URL is not code/session_<X>. The docs
        # relation is url == "https://claude.ai/code/" + id.
        body = json.dumps({
            "type": "routine_fire",
            "claude_code_session_id": "cse_01TestOnlyAbc",
            "claude_code_session_url": "https://claude.ai/code/cse_01TestOnlyAbc"})
        proc = self.run_step("200", body)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        out = proc.stdout + proc.stderr
        self.assertNotIn("01TestOnlyAbc", out)
        self.assertIn("- Session: https://claude.ai/code/cse_01TestOnlyAbc",
                      self.summary.read_text())

    @unittest.skipUnless(shutil.which("jq"), "needs jq on PATH")
    def test_a_cse_id_with_a_session_url_is_accepted(self):
        # list_runs shows the first fire (run 37518720233) as cse_<X> with a
        # URL on session_<X>; the fire response itself was never seen, so
        # this relation is accepted as a possibility, not an observation.
        body = json.dumps({
            "type": "routine_fire",
            "claude_code_session_id": "cse_01TestOnlyAbc",
            "claude_code_session_url": "https://claude.ai/code/session_01TestOnlyAbc"})
        proc = self.run_step("200", body)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        out = proc.stdout + proc.stderr
        self.assertNotIn("01TestOnlyAbc", out)
        self.assertIn("- Session: https://claude.ai/code/session_01TestOnlyAbc",
                      self.summary.read_text())

    @unittest.skipUnless(shutil.which("jq"), "needs jq on PATH")
    def test_success_sends_eval_fields_and_keeps_the_bearer_off_argv(self):
        proc = self.run_step("200", self.ok_body(note="BODY-SENTINEL"))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        argv = json.loads((self.log / "argv.json").read_text())
        self.assertFalse(any(self.BEARER in a for a in argv))
        self.assertIn("anthropic-version: 2023-06-01", argv)
        self.assertEqual(argv[argv.index("--header") + 1], "@-")
        self.assertEqual((self.log / "stdin").read_text(),
                         f"Authorization: Bearer {self.BEARER}\n")
        body = json.loads(argv[argv.index("--data-binary") + 1])
        self.assertEqual(set(body), {"text"})
        self.assertEqual(json.loads(body["text"]), {
            "mode": "eval",
            "run_id": "20261006T120000Z-a1b2c3", "fixture": FIXTURE,
            "arms": "both", "trials": 2})
        out = proc.stdout + proc.stderr
        self.assertNotIn(self.BEARER, out)
        self.assertNotIn("BODY-SENTINEL", out)
        self.assertNotIn(self.SESSION, out, "the session link goes to the summary only")
        self.assertIn("20261006T120000Z-a1b2c3", out)
        summary = self.summary.read_text()
        self.assertIn(f"https://claude.ai/code/{self.SESSION}", summary)
        self.assertIn("claude/eval-20261006T120000Z-a1b2c3", summary)

    @unittest.skipUnless(shutil.which("jq"), "needs jq on PATH")
    def test_bare_sends_arms_bare_and_its_trials(self):
        proc = self.run_step("200", self.ok_body(), ARMS="bare", TRIALS="3",
                             MODEL="claude-sonnet-5-5")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        argv = json.loads((self.log / "argv.json").read_text())
        body = json.loads(argv[argv.index("--data-binary") + 1])
        self.assertEqual(json.loads(body["text"]), {
            "mode": "eval", "run_id": "20261006T120000Z-a1b2c3", "fixture": FIXTURE,
            "arms": "bare", "trials": 3, "model": "claude-sonnet-5-5"})
        self.assertIn("arms: `bare`", self.summary.read_text())

    @unittest.skipUnless(shutil.which("jq"), "needs jq on PATH")
    def test_scaffold_sends_exactly_mode_run_id_candidate_and_snapshot(self):
        # FIXTURE, ARMS and TRIALS are set to junk here: scaffold mode must
        # send none of them, whatever they hold.
        proc = self.run_step("200", self.ok_body(), MODE="scaffold",
                             CANDIDATE=CANDIDATE, FIXTURE="FIXTURE-SENTINEL",
                             ARMS="ARMS-SENTINEL", TRIALS="not-a-number")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        argv = json.loads((self.log / "argv.json").read_text())
        self.assertFalse(any(self.BEARER in a for a in argv))
        self.assertEqual((self.log / "stdin").read_text(),
                         f"Authorization: Bearer {self.BEARER}\n")
        body = json.loads(argv[argv.index("--data-binary") + 1])
        self.assertEqual(set(body), {"text"})
        payload = json.loads(body["text"])
        self.assertEqual(payload, {"mode": "scaffold",
                                   "run_id": "20261006T120000Z-a1b2c3",
                                   "candidate": CANDIDATE,
                                   "issue_snapshot": None})
        self.assertEqual(list(payload),
                         ["mode", "run_id", "candidate", "issue_snapshot"])
        out = proc.stdout + proc.stderr
        self.assertNotIn("SENTINEL", out)
        summary = self.summary.read_text()
        self.assertNotIn("SENTINEL", summary)
        self.assertIn(CANDIDATE, summary)
        self.assertIn("claude/scaffold-", summary)
        self.assertNotIn("claude/eval-", summary)

    def sent_payload(self):
        argv = json.loads((self.log / "argv.json").read_text())
        return json.loads(json.loads(argv[argv.index("--data-binary") + 1])["text"])

    @unittest.skipUnless(shutil.which("jq"), "needs jq on PATH")
    def test_scaffold_carries_the_snapshot_object_as_json(self):
        snap = {"repo": "example/toy", "pr": 7, "issue": 3,
                "title": 'Quote " and $(x)', "body": "line\r\n`y` \\ é\n",
                "issue_created_at": "2026-09-01T09:00:00Z",
                "issue_last_edited_at": None,
                "first_commit_at": "2026-09-01T10:00:00Z"}
        self.snapshot.write_text(json.dumps(snap), encoding="utf-8")
        proc = self.run_step("200", self.ok_body(), MODE="scaffold",
                             CANDIDATE=CANDIDATE)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = self.sent_payload()
        self.assertEqual(payload["issue_snapshot"], snap)
        self.assertEqual(list(payload),
                         ["mode", "run_id", "candidate", "issue_snapshot"])
        out = proc.stdout + proc.stderr
        self.assertNotIn("Quote", out)

    @unittest.skipUnless(shutil.which("jq"), "needs jq on PATH")
    def test_scaffold_refuses_a_missing_or_malformed_snapshot(self):
        for content in (None, "", "[]", '"text"', "1", "{} {}", "{not json"):
            with self.subTest(content=content):
                if content is None:
                    self.snapshot.unlink(missing_ok=True)
                else:
                    self.snapshot.write_text(content, encoding="utf-8")
                proc = self.run_step("200", self.ok_body(), MODE="scaffold",
                                     CANDIDATE=CANDIDATE)
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn("issue snapshot", proc.stdout)
                self.assertFalse((self.log / "calls").exists())
        proc = self.run_step("200", self.ok_body(), MODE="scaffold",
                             CANDIDATE=CANDIDATE, ISSUE_SNAPSHOT_FILE="")
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse((self.log / "calls").exists())

    @unittest.skipUnless(shutil.which("jq"), "needs jq on PATH")
    def test_a_payload_over_the_fire_apis_text_cap_is_refused_not_truncated(self):
        # https://platform.claude.com/docs/en/api/claude-code/routines-fire:
        # `text` is "Maximum 65,536 characters" (400 above it). The cap is on
        # UTF-8 bytes, never fewer than characters by any count.
        def snap(body):
            return {"repo": "example/toy", "pr": 7, "issue": 3, "title": "t",
                    "body": body, "issue_created_at": "2026-09-01T09:00:00Z",
                    "issue_last_edited_at": None,
                    "first_commit_at": "2026-09-01T10:00:00Z"}
        # Measure the payload around the body, then size the body to land
        # exactly on the cap and one byte over it.
        self.snapshot.write_text(json.dumps(snap("")), encoding="utf-8")
        proc = self.run_step("200", self.ok_body(), MODE="scaffold", CANDIDATE=CANDIDATE)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        argv = json.loads((self.log / "argv.json").read_text())
        base = len(json.loads(argv[argv.index("--data-binary") + 1])["text"].encode("utf-8"))
        for extra, ok in ((65536 - base, True), (65536 - base + 1, False)):
            with self.subTest(extra=extra):
                (self.log / "calls").unlink(missing_ok=True)
                self.snapshot.write_text(json.dumps(snap("x" * extra)), encoding="utf-8")
                proc = self.run_step("200", self.ok_body(), MODE="scaffold",
                                     CANDIDATE=CANDIDATE)
                if ok:
                    self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                    self.assertEqual(len(self.sent_payload()["issue_snapshot"]["body"]), extra)
                else:
                    self.assertNotEqual(proc.returncode, 0)
                    self.assertIn("over the fire API's 65,536-character", proc.stdout)
                    self.assertFalse((self.log / "calls").exists())
                    self.assertNotIn("xxxx", proc.stdout + proc.stderr)
        # Multi-byte text counts in bytes: 2-byte characters reach the cap
        # at half the count.
        self.snapshot.write_text(json.dumps(snap("é" * (65536 // 2))), encoding="utf-8")
        proc = self.run_step("200", self.ok_body(), MODE="scaffold", CANDIDATE=CANDIDATE)
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse((self.log / "calls").exists())

    @unittest.skipUnless(shutil.which("jq"), "needs jq on PATH")
    def test_no_snapshot_is_smuggled_into_eval_or_improve_mode(self):
        self.snapshot.write_text(json.dumps({"issue": 3, "title": "SNAP-SENTINEL"}),
                                 encoding="utf-8")
        for mode, extra in (("eval", {}), ("improve", {"SKILL": SKILL})):
            with self.subTest(mode=mode):
                proc = self.run_step("200", self.ok_body(), MODE=mode, **extra)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                payload = self.sent_payload()
                self.assertNotIn("issue_snapshot", payload)
                self.assertNotIn("SNAP-SENTINEL", json.dumps(payload))

    @unittest.skipUnless(shutil.which("jq"), "needs jq on PATH")
    def test_improve_sends_exactly_five_keys(self):
        # FIXTURE, ARMS and CANDIDATE hold junk: improve mode sends none of
        # them. `holdout` is null when empty, never "".
        for holdout, sent in ((HOLDOUT, HOLDOUT), ("", None)):
            with self.subTest(holdout=holdout):
                proc = self.run_step(
                    "200", self.ok_body(), MODE="improve", SKILL=SKILL,
                    TRIALS="3", HOLDOUT=holdout, FIXTURE="FIXTURE-SENTINEL",
                    ARMS="ARMS-SENTINEL", CANDIDATE="CANDIDATE-SENTINEL")
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                argv = json.loads((self.log / "argv.json").read_text())
                self.assertFalse(any(self.BEARER in a for a in argv))
                self.assertEqual((self.log / "stdin").read_text(),
                                 f"Authorization: Bearer {self.BEARER}\n")
                body = json.loads(argv[argv.index("--data-binary") + 1])
                self.assertEqual(set(body), {"text"})
                payload = json.loads(body["text"])
                self.assertEqual(payload, {
                    "run_id": "20261006T120000Z-a1b2c3", "mode": "improve",
                    "skill": SKILL, "trials": 3, "holdout": sent})
                self.assertEqual(list(payload),
                                 ["run_id", "mode", "skill", "trials", "holdout"])
                self.assertIs(type(payload["trials"]), int)
                out = proc.stdout + proc.stderr
                self.assertNotIn("SENTINEL", out)
                self.assertNotIn(self.BEARER, out)
                summary = self.summary.read_text()
                self.assertNotIn("SENTINEL", summary)
                self.assertIn("improve mode", summary)
                self.assertIn(f"`{SKILL}`", summary)
                self.assertIn("claude/eval-improve-20261006T120000Z-a1b2c3", summary)
                self.assertIn("routine-improve-gate.yml", summary)
                self.assertIn(f"https://claude.ai/code/{self.SESSION}", summary)
                if holdout:
                    self.assertIn(f"`{HOLDOUT}`", summary)
                else:
                    self.assertIn("Holdout: none", summary)

    @unittest.skipUnless(shutil.which("jq"), "needs jq on PATH")
    def test_guidance_improve_sends_exactly_five_keys(self):
        # FIXTURE, ARMS, CANDIDATE and SKILL hold junk: guidance-improve mode
        # sends none of them. `holdout` is null when empty, never "".
        for holdout, sent in ((GHOLDOUT, GHOLDOUT), ("", None)):
            with self.subTest(holdout=holdout):
                proc = self.run_step(
                    "200", self.ok_body(), MODE="guidance-improve", SECTION=SECTION,
                    TRIALS="3", HOLDOUT=holdout, FIXTURE="FIXTURE-SENTINEL",
                    ARMS="ARMS-SENTINEL", CANDIDATE="CANDIDATE-SENTINEL",
                    SKILL="SKILL-SENTINEL")
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                argv = json.loads((self.log / "argv.json").read_text())
                self.assertFalse(any(self.BEARER in a for a in argv))
                body = json.loads(argv[argv.index("--data-binary") + 1])
                payload = json.loads(body["text"])
                self.assertEqual(list(payload),
                                 ["run_id", "mode", "section", "trials", "holdout"])
                self.assertEqual(payload, {
                    "run_id": "20261006T120000Z-a1b2c3", "mode": "guidance-improve",
                    "section": SECTION, "trials": 3, "holdout": sent})
                self.assertIs(type(payload["trials"]), int)
                out = proc.stdout + proc.stderr
                self.assertNotIn("SENTINEL", out)
                self.assertNotIn(self.BEARER, out)
                summary = self.summary.read_text()
                self.assertNotIn("SENTINEL", summary)
                self.assertIn("guidance-improve mode", summary)
                self.assertIn(f"`{SECTION}`", summary)
                self.assertIn("claude/eval-guidance-improve-20261006T120000Z-a1b2c3",
                              summary)
                self.assertIn("routine-guidance-improve-gate.yml", summary)

    @unittest.skipUnless(shutil.which("jq"), "needs jq on PATH")
    def test_values_are_json_encoded_by_jq(self):
        # Defense in depth past validation: a value with JSON metacharacters
        # arrives intact as one string, never as extra keys.
        tricky = 'x", "evil": "1'
        for mode, extra, key in (("scaffold", {"CANDIDATE": tricky}, "candidate"),
                                 ("eval", {"FIXTURE": tricky}, "fixture"),
                                 ("improve", {"SKILL": tricky}, "skill"),
                                 ("improve", {"SKILL": SKILL, "HOLDOUT": tricky},
                                  "holdout")):
            with self.subTest(mode=mode, key=key):
                proc = self.run_step("200", self.ok_body(), MODE=mode, **extra)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                argv = json.loads((self.log / "argv.json").read_text())
                body = json.loads(argv[argv.index("--data-binary") + 1])
                payload = json.loads(body["text"])
                self.assertEqual(payload[key], tricky)
                self.assertNotIn("evil", payload)

    @unittest.skipUnless(shutil.which("jq"), "needs jq on PATH")
    def test_the_body_is_the_output_of_one_jq_null_input_call(self):
        jq = self.bin / "jq"
        jq.write_text(FAKE_JQ, encoding="utf-8")
        jq.chmod(jq.stat().st_mode | stat.S_IXUSR)
        for mode, extra in (("eval", {}), ("scaffold", {"CANDIDATE": CANDIDATE}),
                            ("improve", {"SKILL": SKILL, "HOLDOUT": HOLDOUT})):
            with self.subTest(mode=mode):
                (self.log / "jq.jsonl").unlink(missing_ok=True)
                proc = self.run_step("200", self.ok_body(), MODE=mode,
                                     REAL_JQ=shutil.which("jq"), **extra)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                argv = json.loads((self.log / "argv.json").read_text())
                sent = argv[argv.index("--data-binary") + 1]
                calls = [json.loads(l) for l in
                         (self.log / "jq.jsonl").read_text().splitlines()]
                builders = [c for c in calls if c["argv"][:2] == ["-n", "-c"]]
                self.assertEqual(len(builders), 1, calls)
                self.assertEqual(builders[0]["stdout"], sent + "\n")
                # Every value reaches jq as an --arg/--argjson operand, never
                # spliced into the filter program.
                program = next(a for a in builders[0]["argv"][2:]
                               if a.startswith("{text:"))
                self.assertNotIn("20261006T120000Z-a1b2c3", program)
                self.assertNotIn(CANDIDATE, program)
                self.assertNotIn(FIXTURE, program)
                self.assertNotIn(SKILL, program)
                self.assertNotIn(HOLDOUT, program)

    def draft_payload(self, **extra):
        return {"run_id": "20261006T120000Z-a1b2c3", "mode": "draft",
                "proposal": {"id": "lesson-0001", "subject": "example-skill",
                             "evidence_count": 3,
                             "examples": ['an "invented" example, $(x) \u00e9'],
                             "repos": ["example/toy"]}, **extra}

    @unittest.skipUnless(shutil.which("jq"), "needs jq on PATH")
    def test_draft_sends_the_payload_file_as_written(self):
        # FIXTURE, ARMS, CANDIDATE and SKILL hold junk: draft mode sends the
        # payload draft_new_skill.py wrote, byte for byte, and nothing else.
        payload_file = self.tmp / "draft-payload.json"
        for declined, extra in (("false", {}), ("true", {"hook_declined": True})):
            with self.subTest(declined=declined):
                payload = self.draft_payload(**extra)
                payload_file.write_text(json.dumps(payload), encoding="utf-8")
                proc = self.run_step(
                    "200", self.ok_body(), MODE="draft", PROPOSAL=PROPOSAL,
                    HOOK_DECLINED=declined, DRAFT_PAYLOAD_FILE=str(payload_file),
                    FIXTURE="FIXTURE-SENTINEL", ARMS="ARMS-SENTINEL",
                    CANDIDATE="CANDIDATE-SENTINEL", SKILL="SKILL-SENTINEL")
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                sent = self.sent_payload()
                self.assertEqual(sent, payload)
                self.assertEqual(list(sent), list(payload))
                out = proc.stdout + proc.stderr
                self.assertNotIn("SENTINEL", out)
                self.assertNotIn("invented", out, "the payload is never printed")
                summary = self.summary.read_text()
                self.assertIn("draft mode", summary)
                self.assertIn(PROPOSAL, summary)
                self.assertIn("claude/draft-skill-20261006T120000Z-a1b2c3", summary)
                self.assertIn("routine-draft-skill-gate.yml", summary)

    @unittest.skipUnless(shutil.which("jq"), "needs jq on PATH")
    def test_draft_refuses_a_payload_that_is_not_this_runs(self):
        payload_file = self.tmp / "draft-payload.json"
        good = self.draft_payload()
        cases = {
            "other_run_id": (json.dumps({**good, "run_id": "20261006T120000Z-ffffff"}), "false"),
            "other_mode": (json.dumps({**good, "mode": "improve"}), "false"),
            "extra_key": (json.dumps({**good, "skill": "x"}), "false"),
            "missing_proposal": (json.dumps({k: v for k, v in good.items() if k != "proposal"}), "false"),
            "proposal_not_object": (json.dumps({**good, "proposal": "x"}), "false"),
            "declined_key_without_input": (json.dumps({**good, "hook_declined": True}), "false"),
            "declined_input_without_key": (json.dumps(good), "true"),
            "declined_false_value": (json.dumps({**good, "hook_declined": False}), "true"),
            "two_values": (json.dumps(good) + json.dumps(good), "false"),
            "not_json": ("{not json", "false"),
            "empty": ("", "false"),
        }
        for label, (content, declined) in cases.items():
            with self.subTest(case=label):
                (self.log / "calls").unlink(missing_ok=True)
                payload_file.write_text(content, encoding="utf-8")
                proc = self.run_step("200", self.ok_body(), MODE="draft",
                                     PROPOSAL=PROPOSAL, HOOK_DECLINED=declined,
                                     DRAFT_PAYLOAD_FILE=str(payload_file))
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn("draft payload", proc.stdout)
                self.assertFalse((self.log / "calls").exists())
        proc = self.run_step("200", self.ok_body(), MODE="draft",
                             PROPOSAL=PROPOSAL, DRAFT_PAYLOAD_FILE="")
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse((self.log / "calls").exists())

    @unittest.skipUnless(shutil.which("jq"), "needs jq on PATH")
    def test_an_unknown_mode_never_calls_the_api(self):
        for mode in ("", "improved", "Scaffold", "Improve"):
            with self.subTest(mode=mode):
                proc = self.run_step("200", self.ok_body(), MODE=mode)
                self.assertNotEqual(proc.returncode, 0)
                self.assertFalse((self.log / "calls").exists())
                self.assertEqual(self.summary.read_text(), "")

    @unittest.skipUnless(shutil.which("jq"), "needs jq on PATH")
    def test_failures_print_only_the_status(self):
        cases = {
            "http_401": ("401", '{"type":"error","error":{"message":"BODY-SENTINEL"}}'),
            "bad_type": ("200", self.ok_body(type="BODY-SENTINEL")),
            "bad_id": ("200", json.dumps({
                "type": "routine_fire",
                "claude_code_session_id": "BODY-SENTINEL; rm",
                "claude_code_session_url": "https://claude.ai/code/x"})),
            "cse_url_other_key": ("200", json.dumps({
                "type": "routine_fire",
                "claude_code_session_id": "cse_BODYSENTINEL",
                "claude_code_session_url": "https://claude.ai/code/session_Other"})),
            "cse_url_other_host": ("200", json.dumps({
                "type": "routine_fire",
                "claude_code_session_id": "cse_BODYSENTINEL",
                "claude_code_session_url": "https://example.com/code/cse_BODYSENTINEL"})),
            "cse_url_trailing_text": ("200", json.dumps({
                "type": "routine_fire",
                "claude_code_session_id": "cse_BODYSENTINEL",
                "claude_code_session_url": "https://claude.ai/code/cse_BODYSENTINEL/x"})),
            # A session_ id has no cse_ spelling: only code/<id> fits.
            "session_id_cse_url": ("200", json.dumps({
                "type": "routine_fire",
                "claude_code_session_id": "session_BODYSENTINEL",
                "claude_code_session_url": "https://claude.ai/code/cse_BODYSENTINEL"})),
            "other_prefix": ("200", json.dumps({
                "type": "routine_fire",
                "claude_code_session_id": "evil_BODYSENTINEL",
                "claude_code_session_url": "https://claude.ai/code/session_BODYSENTINEL"})),
            "url_mismatch": ("200", self.ok_body(
                claude_code_session_url="https://example.com/BODY-SENTINEL")),
            "not_json": ("200", "BODY-SENTINEL"),
            "http_500": ("500", '{"type":"error","error":{"message":"BODY-SENTINEL"}}'),
        }
        for label, (status, body) in cases.items():
            with self.subTest(case=label):
                proc = self.run_step(status, body)
                self.assertNotEqual(proc.returncode, 0)
                out = proc.stdout + proc.stderr
                self.assertIn(f"status: {status}", out)
                self.assertNotIn("BODY-SENTINEL", out)
                self.assertNotIn("BODYSENTINEL", out)
                self.assertNotIn(self.BEARER, out)
                self.assertEqual(self.summary.read_text(), "")

    @unittest.skipUnless(shutil.which("jq"), "needs jq on PATH")
    def test_a_refused_body_prints_only_its_shape(self):
        ok_url = "https://claude.ai/code/session_X"
        cases = {
            "not_json": ("BODY-SENTINEL not json",
                         ["body_is_json=no"], ["top_level_keys"]),
            "wrapped": (json.dumps({"data": {
                "type": "routine_fire", "claude_code_session_id": "BODY-SENTINEL",
                "claude_code_session_url": ok_url}}),
                ["body_is_json=yes", "top_level_type=object",
                 "type_is_routine_fire=missing", "id_type=null", "id_length=0",
                 "id_prefix=none", "url_starts_with_claude_code=no",
                 "top_level_keys=1", "has_wrapper_key=yes"], []),
            "missing_type": (json.dumps({
                "claude_code_session_id": "session_X",
                "claude_code_session_url": ok_url}),
                ["type_is_routine_fire=missing", "id_type=string",
                 "id_length=9", "id_prefix=session_",
                 "url_starts_with_claude_code=yes",
                 "url_equals_code_plus_id=yes",
                 "url_equals_code_session_suffix=yes", "top_level_keys=2",
                 "has_wrapper_key=no"], []),
            "wrong_type": (json.dumps({
                "type": "BODY-SENTINEL", "claude_code_session_id": "session_X",
                "claude_code_session_url": ok_url}),
                ["type_is_routine_fire=no", "url_equals_code_plus_id=yes",
                 "url_equals_code_session_suffix=yes"], []),
            "wrong_type_documented_url": (json.dumps({
                "type": "BODY-SENTINEL", "claude_code_session_id": "cse_X",
                "claude_code_session_url": "https://claude.ai/code/cse_X"}),
                ["type_is_routine_fire=no", "id_prefix=cse_",
                 "url_equals_code_plus_id=yes",
                 "url_equals_code_session_suffix=no"], []),
            "cse_url_other_key": (json.dumps({
                "type": "routine_fire", "claude_code_session_id": "cse_X",
                "claude_code_session_url": "https://claude.ai/code/cse_BODY"}),
                ["url_equals_code_plus_id=no",
                 "url_equals_code_session_suffix=no"], ["cse_BODY"]),
            "cse_with_bad_url": (json.dumps({
                "type": "routine_fire", "claude_code_session_id": "cse_01Abc",
                "claude_code_session_url": "https://example.com/BODY-SENTINEL"}),
                ["type_is_routine_fire=yes", "id_type=string", "id_length=9",
                 "id_prefix=cse_", "url_starts_with_claude_code=no",
                 "url_equals_code_plus_id=no",
                 "url_equals_code_session_suffix=no"], []),
            "numeric_id": (json.dumps({
                "type": "routine_fire", "claude_code_session_id": 12345,
                "claude_code_session_url": ok_url}),
                ["id_type=number", "id_length=0", "id_prefix=none"], []),
            "free_form_id": (json.dumps({
                "type": "routine_fire",
                "claude_code_session_id": "BODY-SENTINEL; rm -rf",
                "claude_code_session_url": ok_url}),
                ["id_type=string", "id_length=21", "id_prefix=other"], []),
            "uppercase_prefix": (json.dumps({
                "type": "routine_fire",
                "claude_code_session_id": "BODY-SENTINEL_x",
                "claude_code_session_url": ok_url}),
                ["id_length=15", "id_prefix=other"], []),
            "eleven_letter_prefix": (json.dumps({
                "type": "routine_fire",
                "claude_code_session_id": "abcdefghijk_x",
                "claude_code_session_url": ok_url}),
                ["id_length=13", "id_prefix=other"], ["id_prefix=abcdefghijk_"]),
            "array_body": ('["BODY-SENTINEL"]',
                ["body_is_json=yes", "top_level_type=array",
                 "type_is_routine_fire=missing", "top_level_keys=0",
                 "has_wrapper_key=no"], []),
        }
        for label, (body, expected, absent) in cases.items():
            with self.subTest(case=label):
                proc = self.run_step("200", body)
                self.assertNotEqual(proc.returncode, 0)
                out = proc.stdout + proc.stderr
                shape = [l for l in out.splitlines()
                         if l.startswith("response shape:")]
                self.assertEqual(len(shape), 1, out)
                for token in expected:
                    self.assertIn(token, shape[0].split(" "))
                for token in absent:
                    self.assertNotIn(token, shape[0])
                self.assertNotIn("BODY-SENTINEL", out)
                self.assertNotIn(self.BEARER, out)
                self.assertEqual(self.summary.read_text(), "")

    @unittest.skipUnless(shutil.which("jq"), "needs jq on PATH")
    @unittest.skipUnless(shutil.which("jq"), "needs jq on PATH")
    def test_the_fire_url_is_built_from_the_trigger_variable(self):
        # C7: the trigger id is the repository variable, not a constant.
        proc = self.run_step("200", self.ok_body(),
                             ROUTINE_TRIGGER_ID="trig_01ExampleOnly")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        argv = json.loads((self.log / "argv.json").read_text())
        self.assertEqual(
            argv[argv.index("--request") + 2],
            "https://api.anthropic.com/v1/claude_code/routines/"
            "trig_01ExampleOnly/fire")

    def test_a_missing_or_malformed_trigger_variable_stops_before_curl(self):
        # C7: an empty variable (GitHub renders an unset one as ""), or
        # one that is not trig_ plus 1-64 letters or digits, never
        # reaches a URL or the API.
        for label, value in (("empty", ""),
                             ("no_prefix", "014ExampleOnly"),
                             ("path", "trig_x/../../other"),
                             ("space", "trig_x y"),
                             ("too_long", "trig_" + "a" * 65)):
            with self.subTest(case=label):
                proc = self.run_step("200", self.ok_body(),
                                     ROUTINE_TRIGGER_ID=value)
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn("ROUTINE_TRIGGER_ID is not set or is not a trig_ id",
                              proc.stdout + proc.stderr)
                self.assertFalse((self.log / "calls").exists())

    def test_a_curl_failure_is_status_000_and_prints_no_body(self):
        # curl exits nonzero with `%{http_code}` already written as 000 on a
        # refused connection or a timeout, and writes nothing on a spawn
        # failure; both must read as 000.
        for label, printed, code in (("timeout", "000", 28), ("no_output", "", 7)):
            with self.subTest(case=label):
                proc = self.run_step(printed, "BODY-SENTINEL", curl_exit=code)
                self.assertNotEqual(proc.returncode, 0)
                out = proc.stdout + proc.stderr
                self.assertIn("fire API status: 000", out)
                self.assertIn("the fire API answered 000", out)
                self.assertNotIn("BODY-SENTINEL", out)
                self.assertNotIn(self.BEARER, out)
                self.assertEqual(self.summary.read_text(), "")

    @unittest.skipUnless(shutil.which("jq"), "needs jq on PATH")
    def test_it_never_retries(self):
        # The fire API has no idempotency key: a second request after a lost
        # response would start a second session. Exactly one curl call, on
        # success and on every failure.
        cases = {
            "success": ("200", self.ok_body(), 0),
            "http_500": ("500", "BODY-SENTINEL", 0),
            "http_429": ("429", "BODY-SENTINEL", 0),
            "http_401": ("401", "BODY-SENTINEL", 0),
            "curl_failure": ("000", "", 28),
            "bad_body": ("200", "BODY-SENTINEL", 0),
        }
        for label, (status, body, code) in cases.items():
            with self.subTest(case=label):
                self.run_step(status, body, curl_exit=code)
                calls = (self.log / "calls").read_text().splitlines()
                self.assertEqual(calls, ["call"])

    def test_the_workflow_asks_for_no_retry(self):
        doc = load(WORKFLOW)
        self.assertNotIn("--retry", self.script)
        self.assertNotIn("--retry-all-errors", self.script)
        for path, value in scalars(doc):
            if path[-1] in ("run",):
                continue
            self.assertNotRegex(str(value), r"(?i)\bretry\b")
        for key_path in (("jobs", "fire"),):
            node = doc
            for key in key_path:
                node = node[key]
            self.assertNotIn("strategy", node)


if __name__ == "__main__":
    unittest.main()
