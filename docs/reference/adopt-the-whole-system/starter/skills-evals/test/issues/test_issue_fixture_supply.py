#!/usr/bin/env python3
"""Fixture supply with no person in the loop (#65; the owner, 2026-10-26: "closed
loop with no human in the loop"; ADR 0003's 2026-10-26 addendum).

`scripts/scaffold_real_work.py` gains the daily workflow's three steps, and
`.github/workflows/routine-fixture-supply.yml` runs them. Pinned here:

  * `pick` skips every candidate the routine would refuse or already has,
    and ranks the rest by subject usage, then fewest fixtures, then newest
    merge; `fired_candidates` reads the fire workflow's run titles;
  * `supply_plan` cuts the picks to MAX_IN_FLIGHT minus the fires still in
    flight and to the allowance guard's room, and fails closed when the
    guard cannot read the spend;
  * `deps_dirs` derives ci.yml's and dev_setup.sh's install lists;
  * `clean_runs`, `promotable` and `promote` count ingested runs with both
    arms, no errored arm and the committed digest, skip onboarding and
    browser-testing, flip `draft:` and append a schedule entry that the
    weekly planner still loads;
  * the workflow, parsed with PyYAML, keeps its trust boundary.

Offline and deterministic: throwaway Git repositories, synthetic results
trees, the allowance state passed in. example.com only.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
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
SUPPLY = WORKFLOWS / "routine-fixture-supply.yml"
FIRE = WORKFLOWS / "routine-eval-fire.yml"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "harness"))
sys.path.insert(0, str(TEST_DIR))
from lockfile_pins import lock_problem  # noqa: E402

import allowance_guard  # noqa: E402
import plan_scheduled_evals as planner  # noqa: E402
import run_eval  # noqa: E402
import scaffold_real_work as scaffold  # noqa: E402

GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.com",
    "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.com",
    "GIT_AUTHOR_DATE": "2026-09-01T11:00:00Z", "GIT_COMMITTER_DATE": "2026-09-01T11:00:00Z",
}


def candidate(key, **changes):
    row = {"key": key, "answer_leak": {"flag": False}, "closing_issues": [1],
           "files_truncated": False, "touches": [], "merged_at": "2026-09-01T00:00:00Z"}
    row.update(changes)
    return row


def state(**changes):
    out = {"cap": 1.0, "spent": 0.0, "pending": 0.0, "reservation": 0.035,
           "runs": 0, "fires": 0, "recent": 0}
    out.update(changes)
    return out


class PickTests(unittest.TestCase):
    def setUp(self):
        self.evals = Path(tempfile.mkdtemp(prefix="fixture-supply-evals-"))
        self.addCleanup(shutil.rmtree, self.evals, ignore_errors=True)

    def fixture(self, rel):
        (self.evals / rel).mkdir(parents=True)
        (self.evals / rel / "fixture.yaml").write_text("subject: x\n", encoding="utf-8")

    def test_skips_what_the_routine_refuses_or_already_has(self):
        self.fixture("real-work/widget-1")
        rows = [
            candidate("example-org__widget__1"),                  # fixture exists
            candidate("example-org__widget__2"),                  # branch exists
            candidate("example-org__widget__3"),                  # fired recently
            candidate("example-org__widget__4", answer_leak={"flag": True}),
            candidate("example-org__widget__5", answer_leak={}),
            candidate("example-org__widget__6", closing_issues=[1, 2]),
            candidate("example-org__widget__7", files_truncated=True),
            candidate("example-org__widget__9", closing_issues=[]),
            candidate("example-org__skills-evals__10"),       # this repository
            candidate("not a key"),
            "not a row",
            candidate("example-org__widget__8"),
        ]
        got = scaffold.pick(rows, self.evals, {"claude/scaffold-widget-2"},
                            {"example-org__widget__3"}, 10)
        self.assertEqual(got, ["example-org__widget__8"])

    def test_ranks_by_usage_then_fewest_fixtures_then_newest_merge(self):
        self.fixture("writing-adrs/a")
        self.fixture("writing-adrs/b")
        self.fixture("workflow-path-audit")
        rows = [
            candidate("example-org__none__1", merged_at="2026-09-09T00:00:00Z"),
            candidate("example-org__adr__2", touches=["adr"]),
            candidate("example-org__wf__3", touches=["workflows"]),
            candidate("example-org__sh__4", touches=["shell"], merged_at="2026-08-01T00:00:00Z"),
            candidate("example-org__sh__5", touches=["shell"], merged_at="2026-08-02T00:00:00Z"),
        ]
        # Fewest fixtures first (shell 0, workflows 1, adr 2), newest merge
        # within a tie, and a candidate with no subject last.
        self.assertEqual(scaffold.pick(rows, self.evals, set(), set(), 10),
                         ["example-org__sh__5", "example-org__sh__4", "example-org__wf__3",
                          "example-org__adr__2", "example-org__none__1"])
        # Usage outranks the fixture count.
        self.assertEqual(scaffold.pick(rows, self.evals, set(), set(), 2,
                                       usage={"writing-adrs": 9}),
                         ["example-org__adr__2", "example-org__sh__5"])
        self.assertEqual(scaffold.pick(rows, self.evals, set(), set(), 0), [])

    def test_fired_candidates_reads_scaffold_fire_titles_only(self):
        titles = ["Fire eval routine: scaffold example-org__widget__9",
                  "Fire eval routine: eval writing-adrs",
                  "Fire eval routine: scaffold not a key",
                  "Something else"]
        self.assertEqual(scaffold.fired_candidates(titles), {"example-org__widget__9"})

    def test_fire_run_name_starts_with_the_prefix_pick_reads(self):
        doc = yaml.safe_load(FIRE.read_text(encoding="utf-8"))
        # The ` bare` marker renders only for an eval fire (ADR 0012's bare
        # baseline addendum), never in scaffold mode.
        bare = "${{ inputs.mode == 'eval' && inputs.arms == 'bare' && ' bare' || '' }}"
        self.assertIn(bare, doc["run-name"])
        rendered = doc["run-name"].replace(bare, "")
        rendered = rendered.replace("${{ inputs.mode }}", "scaffold")
        # A scaffold fire sends no model, so the folded title ends in a space.
        rendered = rendered.replace("${{ inputs.model }}", "")
        for name in ("candidate", "fixture", "skill", "section"):
            rendered = rendered.replace("${{ inputs.%s }}" % name,
                                        "example-org__widget__9" if name == "candidate" else "")
        self.assertEqual(scaffold.fired_candidates([rendered]), {"example-org__widget__9"})


class SupplyPlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fixture-supply-plan-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        rows = [candidate(f"example-org__widget__{n}", merged_at=f"2026-09-0{n}T00:00:00Z")
                for n in range(1, 6)]
        self.candidates = self.tmp / "candidates.json"
        self.candidates.write_text(json.dumps({"candidates": rows}), encoding="utf-8")
        self.branches = self.tmp / "branches.txt"
        self.branches.write_text("0123\trefs/heads/claude/scaffold-widget-5\n", encoding="utf-8")
        self.fired = self.tmp / "fired.txt"
        self.fired.write_text("Fire eval routine: scaffold example-org__widget__4\n",
                              encoding="utf-8")

    def plan(self, accounting, limit=3):
        with mock.patch.object(scaffold, "allowance_state", **accounting):
            return scaffold.supply_plan(self.candidates, self.branches, self.fired,
                                        self.tmp, limit)

    def keys(self, plan):
        return [row["candidate"] for row in plan["include"]]

    def test_picks_newest_unfired_up_to_the_limit(self):
        plan = self.plan({"return_value": state()})
        self.assertEqual(self.keys(plan), ["example-org__widget__3", "example-org__widget__2",
                                           "example-org__widget__1"])
        self.assertEqual([row["slot"] for row in plan["include"]], [0, 1, 2])
        self.assertIn("weekly eval allowance", plan["line"])

    def test_fires_in_flight_and_the_allowance_cut_it(self):
        self.assertEqual(len(self.plan({"return_value": state(recent=2)})["include"]), 1)
        self.assertEqual(self.plan({"return_value": state(recent=3)})["include"], [])
        self.assertEqual(len(self.plan({"return_value": state(spent=0.93)})["include"]), 2)
        self.assertEqual(self.plan({"return_value": state(spent=1.0)})["include"], [])

    def test_an_unreadable_allowance_plans_nothing(self):
        with self.assertRaises(scaffold.ScaffoldError):
            self.plan({"side_effect": allowance_guard.GuardError("status 502")})
        with self.assertRaises(scaffold.ScaffoldError):
            self.plan({"return_value": state()}, limit=0)

    def test_cli_writes_only_the_matrix_and_count_to_stdout(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(scaffold, "allowance_state", return_value=state(recent=2)), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = scaffold.main(["pick", "--candidates", str(self.candidates),
                                  "--branches", str(self.branches), "--fired", str(self.fired),
                                  "--results-tree", str(self.tmp)])
        self.assertEqual(code, 0)
        lines = out.getvalue().splitlines()
        self.assertEqual(lines, [
            'matrix={"include":[{"candidate":"example-org__widget__3","slot":0}]}', "count=1"])
        self.assertIn("weekly eval allowance", err.getvalue())


class DepsDirsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="fixture-supply-deps-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def write(self, name, deps):
        (self.root / name).mkdir()
        (self.root / name / "fixture.yaml").write_text(
            yaml.safe_dump({"subject": "any", "deps": deps}), encoding="utf-8")

    def test_lists_each_managers_dirs(self):
        self.write("b-2", [{"manager": "npm", "dir": "."}])
        self.write("a-1", [{"manager": "npm", "dir": "web"}, {"manager": "bundler", "dir": "."}])
        self.write("c-3", None)
        self.assertEqual(scaffold.deps_dirs("npm", self.root), ["a-1/seed/web", "b-2/seed"])
        self.assertEqual(scaffold.deps_dirs("bundler", self.root), ["a-1/seed"])

    def test_refuses_a_bad_manager_or_dir(self):
        with self.assertRaises(scaffold.ScaffoldError):
            scaffold.deps_dirs("pip", self.root)
        for bad in ("../x", "/abs", 3):
            with self.subTest(dir=bad):
                shutil.rmtree(self.root / "a-1", ignore_errors=True)
                self.write("a-1", [{"manager": "npm", "dir": bad}])
                with self.assertRaises(scaffold.ScaffoldError):
                    scaffold.deps_dirs("npm", self.root)

    def test_matches_the_committed_fixtures(self):
        # The real tree: every npm or bundler dir names an existing seed dir.
        for manager in scaffold.DEPS_MANAGERS:
            for rel in scaffold.deps_dirs(manager):
                with self.subTest(manager=manager, dir=rel):
                    self.assertTrue((REPO_ROOT / "evals" / "real-work" / rel).is_dir())


class PromotionTests(unittest.TestCase):
    """A throwaway checkout with a flat skill draft, a real-work draft, an
    onboarding draft and a reviewed schedule, and a results tree beside it."""

    AS_OF = "20261020T000000Z"

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="fixture-supply-promote-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.tree = self.root.parent / (self.root.name + "-results")
        self.addCleanup(shutil.rmtree, self.tree, ignore_errors=True)
        evals = self.root / "evals"
        self.write("evals/reviewed-skill/fixture.yaml", "subject: reviewed-skill\nprompt: Hi.\n")
        self.write("evals/toy-skill/fixture.yaml",
                   "# A draft skill fixture.\nsubject: toy-skill\ndraft: true\nprompt: Hi.\n")
        self.write("evals/real-work/toy-7/fixture.yaml",
                   "subject: any\ndraft: true\nprompt: Fix it.\n")
        self.write("evals/onboarding/first-run/fixture.yaml",
                   "subject: onboarding\ndraft: true\nprompt: Hi.\n")
        self.schedule = evals / "scheduled.yml"
        self.write("evals/scheduled.yml",
                   'fixtures:\n  - path: evals/reviewed-skill\n    readiness: "Reviewed."\n')
        self.git("init", "-q", "-b", "main")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "fixtures")

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.root, check=True,
                       env={**os.environ, **GIT_ENV}, capture_output=True)

    def digest(self, rel):
        value = run_eval.fixture_digest(self.root / rel)
        self.assertIsNotNone(value)
        return value

    def run_result(self, key, stamp, *, digest, fixture=None, arms=("with_skill", "without_skill"),
                   run="20261001T000000Z-abc123", **summary):
        for arm in arms:
            parts = ["routine-results", run, key, stamp, *([fixture] if fixture else []), arm]
            path = self.tree.joinpath(*parts, "summary.json")
            path.parent.mkdir(parents=True, exist_ok=True)
            body = {"arm": arm, "role": run_eval._ROLES[arm], "error": None, "errors": 0,
                    "n": 1, "fixture_digest": digest}
            body.update(summary.get(arm, {}))
            path.write_text(json.dumps(body), encoding="utf-8")

    def count(self, rel):
        return scaffold.clean_runs(self.tree, self.root / rel, self.root / "evals", self.AS_OF)

    def test_counts_only_clean_paired_runs_of_the_committed_content(self):
        rel = "evals/toy-skill"
        digest = self.digest(rel)
        self.run_result("toy-skill", "20261002T000000Z", digest=digest)
        self.run_result("toy-skill", "20261003T000000Z", digest=digest, arms=("with_skill",))
        self.run_result("toy-skill", "20261004T000000Z", digest=digest,
                        without_skill={"errors": 1})
        self.run_result("toy-skill", "20261005T000000Z", digest=digest,
                        with_skill={"error": "timeout"})
        self.run_result("toy-skill", "20261006T000000Z", digest="0" * 64)
        self.run_result("toy-skill", "20261007T000000Z", digest=digest, with_skill={"n": 0})
        self.run_result("toy-skill", "20261030T000000Z", digest=digest)   # after as_of
        self.run_result("other-skill", "20261008T000000Z", digest=digest)
        self.assertEqual(self.count(rel), 1)
        self.run_result("toy-skill", "20261009T000000Z", digest=digest,
                        run="20261009T000000Z-def456")
        self.assertEqual(self.count(rel), 2)

    def test_a_real_work_fixture_counts_under_its_own_name(self):
        rel = "evals/real-work/toy-7"
        digest = self.digest(rel)
        self.run_result("toy-7", "20261002T000000Z", digest=digest)
        self.run_result("real-work", "20261003T000000Z", digest=digest, fixture="toy-7")
        self.assertEqual(self.count(rel), 1)

    def test_promotes_flips_and_schedules_a_clean_draft(self):
        for rel, key in (("evals/toy-skill", "toy-skill"),
                         ("evals/onboarding/first-run", "onboarding")):
            digest = self.digest(rel)
            fixture = None if key == "toy-skill" else "first-run"
            for day in ("02", "03"):
                self.run_result(key, f"202610{day}T000000Z", digest=digest, fixture=fixture)
        self.assertEqual(self.count("evals/onboarding/first-run"), 2)
        found = scaffold.promotable(self.root, self.tree, self.AS_OF)
        self.assertEqual(found, [("evals/toy-skill", 2)])
        self.assertEqual(scaffold.promote(self.root, found, self.AS_OF), ["evals/toy-skill"])
        self.assertEqual((self.root / "evals/toy-skill/fixture.yaml").read_text(encoding="utf-8"),
                         "# A draft skill fixture.\nsubject: toy-skill\ndraft: false\nprompt: Hi.\n")
        committed = planner.committed_fixtures(self.root)
        self.assertEqual(planner.scheduled_fixtures(self.schedule, committed),
                         ["evals/reviewed-skill", "evals/toy-skill"])
        entry = yaml.safe_load(self.schedule.read_text(encoding="utf-8"))["fixtures"][-1]
        self.assertEqual(entry["path"], "evals/toy-skill")
        self.assertTrue(entry["readiness"].startswith(
            f"{scaffold.PROMOTED_NOTE} on 2026-10-20 by scaffold_real_work.py promote: 2 "))
        # Promoted, it is no longer a draft, so a second pass finds nothing.
        self.assertEqual(scaffold.promotable(self.root, self.tree, self.AS_OF), [])

    def test_one_clean_run_is_not_enough(self):
        self.run_result("toy-skill", "20261002T000000Z", digest=self.digest("evals/toy-skill"))
        self.assertEqual(scaffold.promotable(self.root, self.tree, self.AS_OF), [])

    def test_refuses_a_draft_line_it_cannot_flip_cleanly(self):
        self.write("evals/toy-skill/fixture.yaml",
                   "subject: toy-skill\ndraft: true\nprompt: |\n  x\ndraft: true\n")
        with self.assertRaises(scaffold.ScaffoldError):
            scaffold.promote(self.root, [("evals/toy-skill", 2)], self.AS_OF)
        self.write("evals/toy-skill/fixture.yaml",
                   "subject: toy-skill\ndraft:   true\nprompt: Hi.\n")
        with self.assertRaises(scaffold.ScaffoldError):
            scaffold.promote(self.root, [("evals/toy-skill", 2)], self.AS_OF)
        # One matching line that is not the `draft:` key: inside a quoted string.
        self.write("evals/toy-skill/fixture.yaml",
                   'subject: toy-skill\nprompt: "Hi.\n\ndraft: true\n"\n')
        with self.assertRaises(scaffold.ScaffoldError):
            scaffold.promote(self.root, [("evals/toy-skill", 2)], self.AS_OF)
        self.assertNotIn("toy-skill", self.schedule.read_text(encoding="utf-8"))


def run_blocks(doc):
    return [(name, step["run"]) for name, job in doc["jobs"].items()
            for step in job.get("steps", []) if "run" in step]


class SupplyWorkflowShapeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = SUPPLY.read_text(encoding="utf-8")
        cls.doc = yaml.safe_load(cls.text)
        cls.jobs = cls.doc["jobs"]

    def step(self, job, needle):
        found = [s for s in self.jobs[job]["steps"] if needle in s.get("run", "")]
        self.assertEqual(len(found), 1, needle)
        return found[0]

    def test_triggers_and_permissions(self):
        self.assertEqual(set(self.doc[True]), {"schedule", "workflow_dispatch"})
        self.assertEqual(self.doc["permissions"], {})
        self.assertNotIn("concurrency", self.doc)
        self.assertEqual({name: job["permissions"] for name, job in self.jobs.items()}, {
            "plan": {"contents": "read", "actions": "read"},
            "dispatch": {"contents": "read", "actions": "write"},
            "promote": {"contents": "read", "pull-requests": "read"}})
        for name, job in self.jobs.items():
            with self.subTest(job=name):
                self.assertNotIn("concurrency", job)
                self.assertIn("github.ref == format('refs/heads/{0}', "
                              "github.event.repository.default_branch)", job["if"])

    def test_every_uses_is_locked(self):
        uses = [s["uses"] for job in self.jobs.values() for s in job["steps"] if "uses" in s]
        self.assertTrue(uses)
        for value in uses:
            with self.subTest(uses=value):
                self.assertIsNone(lock_problem(value, SUPPLY))

    def test_no_expressions_in_run_blocks(self):
        blocks = run_blocks(self.doc)
        self.assertTrue(blocks)
        for job, block in blocks:
            with self.subTest(job=job):
                self.assertNotIn("${{", block)

    def test_dispatch_reuses_the_fire_workflow_one_at_a_time(self):
        job = self.jobs["dispatch"]
        self.assertEqual(job["strategy"]["max-parallel"], 1)
        block = self.step("dispatch", "gh workflow run")["run"]
        self.assertIn("gh workflow run routine-eval-fire.yml", block)
        self.assertIn("--raw-field mode=scaffold", block)
        self.assertIn("sleep 60", block)

    def test_constants_match_the_script(self):
        listing = self.step("plan", "gh api --paginate")["run"]
        self.assertIn(f"-{scaffold.REFIRE_AFTER_DAYS} days", listing)
        pick = self.step("plan", "scaffold_real_work.py pick")["run"]
        self.assertIn(f"--max {scaffold.MAX_IN_FLIGHT}", pick)
        self.assertIn("display_title", listing)

    def test_the_app_key_reaches_only_promotes_mint_step(self):
        holders = [(name, s.get("id")) for name, job in self.jobs.items()
                   for s in job["steps"] if "ROSTER_APP_PRIVATE_KEY" in json.dumps(s)]
        self.assertEqual(holders, [("promote", "app-token")])
        steps = self.jobs["promote"]["steps"]
        ids = [s.get("id") for s in steps]
        self.assertLess(ids.index("promote"), ids.index("app-token"))
        mint = steps[ids.index("app-token")]
        self.assertTrue(mint["continue-on-error"])
        self.assertEqual(mint["with"]["permission-contents"], "write")

    def test_the_miner_reads_the_fleet_with_the_read_only_registry_token(self):
        holders = [(name, s.get("id")) for name, job in self.jobs.items()
                   for s in job["steps"] if "REGISTRY_READ_APP_PEM" in json.dumps(s)]
        self.assertEqual(holders, [("plan", "fleet-token")])
        mint = next(s for s in self.jobs["plan"]["steps"] if s.get("id") == "fleet-token")
        self.assertEqual(mint["with"]["repositories"], "acsi-agentskills-private")
        self.assertEqual(mint["with"]["permission-contents"], "read")
        self.assertNotIn("continue-on-error", mint)
        step = self.step("plan", "mine_real_work.py mine")
        self.assertEqual(step["env"]["GH_TOKEN"], "${{ steps.fleet-token.outputs.token }}")

    def test_promotion_pushes_only_what_promote_writes_and_arms_that_sha(self):
        step = self.step("promote", "app_gh pr merge")
        self.assertEqual(step["env"]["APP_TOKEN"], "${{ steps.app-token.outputs.token }}")
        self.assertEqual(step["env"]["GH_TOKEN"], "${{ github.token }}")
        block = step["run"]
        self.assertIn("evals/scheduled.yml|evals/*/fixture.yaml) ;;", block)
        self.assertIn('push -q origin "HEAD:refs/heads/${branch}"', block)
        self.assertIn('branch="fixture-promotion/${RUN_ID}"', block)
        self.assertIn('--auto --merge --match-head-commit "$sha"', block)
        self.assertNotIn("--squash", block)
        self.assertNotIn("--admin", block)


if __name__ == "__main__":
    unittest.main()
