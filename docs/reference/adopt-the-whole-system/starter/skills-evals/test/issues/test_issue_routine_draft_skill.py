#!/usr/bin/env python3
"""Draft mode's workflows: lessons in, a hook proposal or a skill draft out.

Three workflows (docs/how-it-works.md, "Draft mode: new skills from lessons,
hooks first"); routine-eval-fire.yml's draft mode is pinned in
test_issue_routine_eval_fire.py, and scripts/draft_new_skill.py in
test_issue_draft_new_skill.py. What is pinned here, parsing the YAML with
PyYAML and running the shell steps that carry logic against a fake `gh`:

  * routine-draft-skill.yml: a daily schedule plus dispatch; `select` holds
    contents/actions/issues read, mints the read-only registry App token
    exactly as propagation.yml does, reads lessons/ and plugins/ of the
    private registry, the public registry, the drafted branches and the
    declined hook proposals, and checks the allowance with `fits`;
    `dispatch` holds actions: write, re-checks the proposal pattern and maps
    `hook_declined` before `gh workflow run`, never echoing a bad value;
  * routine-draft-skill-pushed.yml: a filtered push with no reach, whose
    branch no other routine signal matches;
  * routine-draft-skill-gate.yml: workflow_run of the signal, the default
    branch's copy only; one validate job with contents: read; the App
    secrets reach only the credential boolean and one mint step; every
    acting job re-validates at the judged sha before it acts; the fixtures
    pull request follows the skill's; hook-first opens an issue with the job
    token's `issues: write` and nothing else; no merge, force or approve;
  * all three: every `uses:` locked in actions.lock at a tag the repo
    already pins, no `${{ }}` in a `run:`, no concurrency group, every
    checkout without persisted credentials.

Discovered and run by test/run_tests.py.
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
from fnmatch import fnmatch
from pathlib import Path

import yaml

TEST_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = TEST_DIR.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
CALLER = WORKFLOWS / "routine-draft-skill.yml"
SIGNAL = WORKFLOWS / "routine-draft-skill-pushed.yml"
GATE = WORKFLOWS / "routine-draft-skill-gate.yml"
OURS = (CALLER, SIGNAL, GATE)
sys.path.insert(0, str(TEST_DIR))
from lockfile_pins import lock_problem  # noqa: E402

PROPOSAL = "proposals/2026-10-26/example-skill__new-skill.json"
BRANCH = "claude/draft-skill-20261026T063000Z-0a1b2c"
DEFAULT_BRANCH_IF = ("github.ref == format('refs/heads/{0}', "
                     "github.event.repository.default_branch)")
PUSH_TARGET = re.compile(r"\bpush\s+(?:[^\n]*?\s)?origin\s+([^\s;]+)")


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def triggers(doc: dict):
    # PyYAML 1.1 reads a bare `on:` key as boolean True.
    return doc.get("on", doc.get(True))


def strings(node, path=()):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from strings(value, path + (str(key),))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from strings(value, path + (str(i),))
    elif isinstance(node, str):
        yield path, node


def run_blocks(doc: dict) -> list[tuple[str, str]]:
    return [(name, step["run"]) for name, job in doc["jobs"].items()
            for step in job["steps"] if "run" in step]


def uses_nodes(node):
    if isinstance(node, yaml.MappingNode):
        for key, value in node.value:
            if isinstance(key, yaml.ScalarNode) and key.value == "uses" \
                    and isinstance(value, yaml.ScalarNode):
                yield value
            else:
                yield from uses_nodes(value)
    elif isinstance(node, yaml.SequenceNode):
        for item in node.value:
            yield from uses_nodes(item)


def step(doc: dict, job: str, name: str) -> dict:
    return next(s for s in doc["jobs"][job]["steps"] if s.get("name") == name)


class AllThreeTests(unittest.TestCase):

    def test_every_uses_is_locked_and_already_pinned_elsewhere(self):
        pinned = set()
        for other in WORKFLOWS.glob("*.yml"):
            if other not in OURS:
                pinned |= {n.value for n in uses_nodes(
                    yaml.compose(other.read_text(encoding="utf-8")))}
        for path in OURS:
            text = path.read_text(encoding="utf-8")
            lines = text.splitlines()
            for node in uses_nodes(yaml.compose(text)):
                with self.subTest(path=path.name, uses=node.value):
                    self.assertIsNone(lock_problem(node.value, path))
                    rest = lines[node.end_mark.line][node.end_mark.column:]
                    self.assertEqual(rest.strip(), "", "a pin carries no trailing comment")
                    self.assertIn(node.value, pinned, "no new action")

    def test_no_expressions_in_run_blocks(self):
        for path in OURS:
            for job, body in run_blocks(load(path)):
                with self.subTest(path=path.name, job=job, body=body[:40]):
                    self.assertNotIn("${{", body, "pass values through env, never inline")

    def test_no_concurrency_and_no_pull_request_trigger(self):
        for path in OURS:
            doc = load(path)
            with self.subTest(path=path.name):
                self.assertNotIn("concurrency", doc)
                self.assertFalse({"pull_request", "pull_request_target"} & set(triggers(doc)))
                self.assertEqual(doc["permissions"], {})
                self.assertNotIn("env", doc)
                for job in doc["jobs"].values():
                    self.assertNotIn("concurrency", job)
                    self.assertNotIn("env", job)

    def test_checkouts_keep_no_credentials(self):
        for path in OURS:
            for job_name, job in load(path)["jobs"].items():
                for s in job["steps"]:
                    if str(s.get("uses", "")).startswith("actions/checkout@"):
                        with self.subTest(path=path.name, job=job_name, step=s.get("name")):
                            self.assertIs(s["with"]["persist-credentials"], False)


class CallerTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.doc = load(CALLER)
        cls.jobs = cls.doc["jobs"]

    def test_daily_schedule_and_dispatch_only(self):
        on = triggers(self.doc)
        self.assertEqual(set(on), {"schedule", "workflow_dispatch"})
        self.assertEqual(on["schedule"], [{"cron": "30 6 * * *"}])

    def test_permissions_are_per_job_and_minimal(self):
        self.assertEqual(set(self.jobs), {"select", "dispatch"})
        self.assertEqual(self.jobs["select"]["permissions"],
                         {"contents": "read", "actions": "read", "issues": "read"})
        self.assertEqual(self.jobs["dispatch"]["permissions"],
                         {"contents": "read", "actions": "write"})
        self.assertEqual(self.jobs["select"]["if"], DEFAULT_BRANCH_IF)
        self.assertIn(DEFAULT_BRANCH_IF, self.jobs["dispatch"]["if"])
        self.assertIn("needs.select.outputs.count == '1'", self.jobs["dispatch"]["if"])
        self.assertIn("needs.select.outputs.fits == 'yes'", self.jobs["dispatch"]["if"])

    def test_the_lessons_token_is_read_only_and_reaches_two_checkouts(self):
        mint = step(self.doc, "select", "Mint the read-only token for the lessons")
        self.assertTrue(mint["uses"].startswith("actions/create-github-app-token@"))
        self.assertEqual(mint["with"], {
            "client-id": "${{ vars.REGISTRY_READ_APP_CLIENT_ID }}",
            "private-key": "${{ secrets.REGISTRY_READ_APP_PEM }}",
            "owner": "example-adopter",
            "repositories": "agentskills-private",
            "permission-contents": "read"})
        secrets = [p for p, v in strings(self.doc) if "secrets." in v]
        self.assertEqual(len(secrets), 1)
        users = [p for p, v in strings(self.doc) if "steps.lessons-token" in v]
        # One env use, the lessons-branch probe; the rest are checkouts.
        probes = [u for u in users if u[4] == "env"]
        self.assertEqual(len(probes), 1)
        self.assertEqual(self.jobs["select"]["steps"][int(probes[0][3])]["id"], "lessons-branch")
        users = [u for u in users if u[4] == "with"]
        self.assertEqual(len(users), 2)
        holders = [self.jobs["select"]["steps"][int(u[3])]["with"] for u in users]
        for holder in holders:
            self.assertEqual(holder["repository"], "example-adopter/agentskills-private")
        # The miner writes proposals to persistent/lessons (main is PR-only);
        # the private skills come from the default branch.
        self.assertEqual(
            sorted((h.get("ref", ""), h["sparse-checkout"], h["path"]) for h in holders),
            sorted([("persistent/lessons", "lessons", "agentskills-private-lessons"),
                    ("", "plugins", "agentskills-private")]))

    def test_select_reads_drafted_branches_declines_and_both_registries(self):
        body = step(self.doc, "select", "Select one new-skill proposal")["run"]
        for needle in ("draft_new_skill.py drafted-subjects --repo .",
                       "draft_new_skill.py select",
                       "--registry agentskills=../agentskills",
                       "--registry agentskills-private=../agentskills-private",
                       "--lessons ../agentskills-private-lessons/lessons",
                       "--evals evals --drafted",
                       '--hook-declined "$RUNNER_TEMP/hook-declined.txt"'):
            self.assertIn(needle, body)
        fetch = step(self.doc, "select", "Fetch the drafted branches")["run"]
        self.assertIn("'+refs/heads/claude/draft-skill-*:refs/remotes/origin/claude/draft-skill-*'", fetch)

    def test_the_drafted_branch_fetch_succeeds_with_no_drafted_branch(self):
        # The first run has no claude/draft-skill-* branch, and a glob
        # refspec that matches nothing fails the fetch (run 40401949160).
        fetch = step(self.doc, "select", "Fetch the drafted branches")["run"]
        with tempfile.TemporaryDirectory() as tmp:
            up, down = Path(tmp) / "up", Path(tmp) / "down"
            env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull,
                   "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                   "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
            subprocess.run(["git", "init", "-q", "-b", "main", str(up)],
                           check=True, env=env, capture_output=True)
            subprocess.run(["git", "-C", str(up), "commit", "-q", "--allow-empty", "-m", "base"],
                           check=True, env=env, capture_output=True)
            subprocess.run(["git", "clone", "-q", f"file://{up}", str(down)],
                           check=True, env=env, capture_output=True)
            done = subprocess.run(["bash", "-c", fetch], cwd=down, env=env, capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
            subprocess.run(["git", "-C", str(up), "branch", "claude/draft-skill-20261026T000000Z-abcdef"],
                           check=True, env=env, capture_output=True)
            done = subprocess.run(["bash", "-c", fetch], cwd=down, env=env, capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
            refs = subprocess.run(["git", "-C", str(down), "for-each-ref", "--format=%(refname)",
                                   "refs/remotes/origin/claude/"],
                                  check=True, env=env, capture_output=True, text=True).stdout.split()
            self.assertEqual(refs, ["refs/remotes/origin/claude/draft-skill-20261026T000000Z-abcdef"])
        outputs = self.jobs["select"]["outputs"]
        self.assertEqual(set(outputs), {"proposal", "count", "hook_declined", "fits"})

    def test_the_allowance_pre_check_runs_only_with_a_pick(self):
        s = step(self.doc, "select", "Check the weekly allowance")
        self.assertEqual(s["if"], "steps.select.outputs.count == '1'")
        self.assertEqual(s["env"], {"GH_TOKEN": "${{ github.token }}"})
        self.assertIn("scripts/allowance_guard.py fits --results-tree", s["run"])


FAKE_GH = """#!/usr/bin/env python3
import json, os, sys
log = os.environ["FAKE_GH_LOG"]
with open(log, "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1:2] == ["api"]:
    sys.stdout.write(os.environ.get("FAKE_GH_OUT", ""))
sys.exit(int(os.environ.get("FAKE_GH_EXIT", "0")))
"""


@unittest.skipUnless(shutil.which("bash"), "needs bash")
class CallerStepTests(unittest.TestCase):
    """The caller's shell steps, run for real against a fake gh."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(FAKE_GH, encoding="utf-8")
        gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
        self.log = self.tmp / "gh.jsonl"
        self.env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                    "HOME": str(self.tmp), "RUNNER_TEMP": str(self.tmp),
                    "GITHUB_REPOSITORY": "example/skills-evals",
                    "FAKE_GH_LOG": str(self.log)}
        self.doc = load(CALLER)

    def run_step(self, name, **env):
        s = step(self.doc, "dispatch" if name == "Dispatch the draft" else "select", name)
        return subprocess.run(["bash", "-c", s["run"]], cwd=self.tmp,
                              env={**self.env, **env}, capture_output=True,
                              text=True, timeout=60)

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(l) for l in self.log.read_text().splitlines()]

    def test_a_missing_lessons_branch_skips_the_checkout_without_failing(self):
        # persistent/lessons exists only after the miner's first push
        # (run 40404287379 failed checking it out).
        checkout = step(self.doc, "select", "Check out the lessons (agentskills-private)")
        self.assertEqual(checkout["if"], "steps.lessons-branch.outputs.present == 'true'")
        self.assertIn("mkdir -p ../agentskills-private-lessons/lessons",
                      step(self.doc, "select", "Select one new-skill proposal")["run"])
        git = self.tmp / "bin" / "git"
        git.write_text('#!/bin/sh\necho "$*" >> "$FAKE_GIT_LOG"\nexit "$FAKE_GIT_EXIT"\n',
                       encoding="utf-8")
        git.chmod(git.stat().st_mode | stat.S_IXUSR)
        for code, rc, present in (("2", 0, "false"), ("0", 0, "true"), ("128", 128, None)):
            with self.subTest(code=code):
                out = self.tmp / f"out-{code}"
                log = self.tmp / f"git-{code}.log"
                done = self.run_step("Look for the lessons branch", GITHUB_OUTPUT=str(out),
                                     LESSONS_TOKEN="example-not-a-token",
                                     FAKE_GIT_EXIT=code, FAKE_GIT_LOG=str(log))
                self.assertEqual(done.returncode, rc, done.stderr)
                self.assertNotIn("example-not-a-token", done.stdout + done.stderr)
                self.assertIn("persistent/lessons", log.read_text())
                self.assertNotIn("example-not-a-token", log.read_text())
                written = out.read_text() if out.exists() else ""
                self.assertEqual(written, f"present={present}\n" if present else "")

    def test_dispatch_sends_the_proposal_and_the_declined_flag(self):
        for flag, sent in (("0", "false"), ("1", "true")):
            with self.subTest(flag=flag):
                self.log.unlink(missing_ok=True)
                proc = self.run_step("Dispatch the draft", DEFAULT_BRANCH="main",
                                PROPOSAL=PROPOSAL, HOOK_DECLINED=flag)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                [call] = self.calls()
                self.assertEqual(call[:2], ["workflow", "run"])
                self.assertIn("routine-eval-fire.yml", call)
                self.assertIn("mode=draft", call)
                self.assertIn(f"proposal={PROPOSAL}", call)
                self.assertIn(f"hook_declined={sent}", call)

    def test_dispatch_refuses_a_bad_value_without_calling_gh(self):
        cases = {
            "not_new_skill": (PROPOSAL.replace("new-skill", "skill-edit"), "0"),
            "dotdot": ("proposals/2026-10-26/../x__new-skill.json", "0"),
            "subst": ("proposals/2026-10-26/$(id)__new-skill.json", "0"),
            "newline": (PROPOSAL + "\nproposals/x", "0"),
            "empty": ("", "0"),
            "flag_empty": (PROPOSAL, ""),
            "flag_word": (PROPOSAL, "true"),
        }
        for label, (proposal, flag) in cases.items():
            with self.subTest(case=label):
                self.log.unlink(missing_ok=True)
                proc = self.run_step("Dispatch the draft", DEFAULT_BRANCH="main",
                                PROPOSAL=proposal, HOOK_DECLINED=flag)
                self.assertNotEqual(proc.returncode, 0)
                self.assertEqual(self.calls(), [])
                if proposal.strip() and label != "flag_empty" and label != "flag_word":
                    self.assertNotIn(proposal, proc.stdout + proc.stderr)

    def test_declined_list_keeps_only_valid_titled_subjects(self):
        titles = "\n".join([
            "Hook proposal: example-skill",
            "Hook proposal: another-one",
            "Hook proposal: Bad-Case",
            "Hook proposal: ../escape",
            "Something else: x",
            "Hook proposal: " + "s" * 65,
            "Hook proposal: $(id)",
        ])
        proc = self.run_step("List the declined hook proposals", GH_TOKEN="t",
                        FAKE_GH_OUT=titles + "\n")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual((self.tmp / "hook-declined.txt").read_text(),
                         "example-skill\nanother-one\n")
        [call] = self.calls()
        self.assertIn("repos/example/skills-evals/issues?labels=hook-proposal&state=closed&per_page=100", call)
        jq = call[call.index("--jq") + 1]
        self.assertIn('.state_reason == "not_planned"', jq)

    def test_declined_list_with_no_issues_is_empty(self):
        proc = self.run_step("List the declined hook proposals", GH_TOKEN="t", FAKE_GH_OUT="")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual((self.tmp / "hook-declined.txt").read_text(), "")

    def test_declined_list_fails_closed_on_an_api_error(self):
        proc = self.run_step("List the declined hook proposals", GH_TOKEN="t",
                        FAKE_GH_OUT='{"message": "Hook proposal: leaked-body"}',
                        FAKE_GH_EXIT="1")
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn("leaked-body", proc.stdout + proc.stderr)


class SignalTests(unittest.TestCase):

    def test_signal_is_a_filtered_push_with_no_reach(self):
        doc = load(SIGNAL)
        on = triggers(doc)
        self.assertEqual(set(on), {"push"})
        self.assertEqual(on["push"]["branches"], ["claude/draft-skill-*"])
        self.assertEqual(on["push"]["paths"], ["draft-skill/**", "evals/**"])
        for job in doc["jobs"].values():
            self.assertNotIn("permissions", job)
            for s in job["steps"]:
                self.assertNotIn("uses", s, "the signal checks nothing out")

    def test_no_other_routine_signal_sees_a_draft_push(self):
        for other in ("routine-eval-results-pushed.yml", "routine-improve-pushed.yml",
                      "routine-scaffold-pushed.yml"):
            with self.subTest(other=other):
                branches = triggers(load(WORKFLOWS / other))["push"]["branches"]
                self.assertFalse(any(fnmatch(BRANCH, b) for b in branches))
        self.assertTrue(fnmatch(BRANCH, triggers(load(SIGNAL))["push"]["branches"][0]))


class GateTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.doc = load(GATE)
        cls.jobs = cls.doc["jobs"]

    def test_runs_on_workflow_run_of_the_signal(self):
        on = triggers(self.doc)
        self.assertEqual(set(on), {"workflow_run", "workflow_dispatch"})
        self.assertEqual(on["workflow_run"]["workflows"], [load(SIGNAL)["name"]])
        self.assertEqual(on["workflow_run"]["types"], ["completed"])
        self.assertEqual(on["workflow_run"]["branches"], ["claude/draft-skill-*"])
        self.assertEqual(set(on["workflow_dispatch"]["inputs"]), {"branch"})
        self.assertIn(DEFAULT_BRANCH_IF, self.jobs["validate"]["if"])
        self.assertIn("github.event.workflow_run.conclusion == 'success'",
                      self.jobs["validate"]["if"])

    def test_job_permissions_are_minimal(self):
        self.assertEqual(self.doc["permissions"], {})
        perms = {name: job["permissions"] for name, job in self.jobs.items()}
        self.assertEqual(perms, {
            "validate": {"contents": "read"},
            "credential": {},
            "skill-pr": {"contents": "read"},
            "fixtures-pr": {"contents": "write", "pull-requests": "write"},
            "hook-issue": {"contents": "read", "issues": "write"},
        })

    def test_jobs_run_by_decision_and_in_order(self):
        def needs(name):
            n = self.jobs[name]["needs"]
            return sorted(n if isinstance(n, list) else [n])
        self.assertEqual(needs("credential"), ["validate"])
        self.assertEqual(self.jobs["credential"]["if"],
                         "needs.validate.outputs.decision == 'accepted'")
        self.assertEqual(needs("skill-pr"), ["credential", "validate"])
        self.assertIn("needs.validate.outputs.decision == 'accepted'", self.jobs["skill-pr"]["if"])
        self.assertIn("needs.credential.outputs.present == 'true'", self.jobs["skill-pr"]["if"])
        # Fixtures only after the skill's pull request was opened.
        self.assertEqual(needs("fixtures-pr"), ["skill-pr", "validate"])
        self.assertEqual(self.jobs["fixtures-pr"]["if"], "needs.skill-pr.outputs.opened == 'true'")
        self.assertEqual(needs("hook-issue"), ["validate"])
        self.assertEqual(self.jobs["hook-issue"]["if"],
                         "needs.validate.outputs.decision == 'hook-first'")

    def test_the_app_secrets_reach_only_the_boolean_and_the_mint(self):
        refs = sorted((p, v.strip()) for p, v in strings(self.doc) if "secrets." in v)
        mint = next(i for i, s in enumerate(self.jobs["skill-pr"]["steps"])
                    if str(s.get("uses", "")).startswith("actions/create-github-app-token@"))
        self.assertEqual(refs, sorted([
            (("jobs", "credential", "steps", "0", "env", "PR_CREDENTIAL_SET"),
             "${{ secrets.EVAL_IMPROVE_APP_CLIENT_ID != '' && secrets.EVAL_IMPROVE_APP_PEM != '' }}"),
            (("jobs", "skill-pr", "steps", str(mint), "with", "client-id"),
             "${{ secrets.EVAL_IMPROVE_APP_CLIENT_ID }}"),
            (("jobs", "skill-pr", "steps", str(mint), "with", "private-key"),
             "${{ secrets.EVAL_IMPROVE_APP_PEM }}"),
        ]))
        with_ = self.jobs["skill-pr"]["steps"][mint]["with"]
        self.assertEqual(with_["repositories"], "agentskills")
        self.assertEqual({k: v for k, v in with_.items() if k.startswith("permission-")},
                         {"permission-contents": "write", "permission-pull-requests": "write"})
        users = [p for p, v in strings(self.doc) if "steps.app-token.outputs" in v]
        self.assertEqual(len(users), 1)
        self.assertEqual(users[0][-2:], ("env", "GH_TOKEN"))

    def test_every_acting_job_revalidates_at_the_judged_sha_first(self):
        for name in ("skill-pr", "fixtures-pr", "hook-issue"):
            steps = self.jobs[name]["steps"]
            ids = [s.get("id") for s in steps]
            with self.subTest(job=name):
                self.assertIn("revalidate", ids)
                reval = steps[ids.index("revalidate")]
                self.assertEqual(reval["env"]["SHA"], "${{ needs.validate.outputs.sha }}")
                self.assertIn('--expect-sha "$SHA"', reval["run"])
                self.assertIn("--registry agentskills=../agentskills", reval["run"])
                actor = next(i for i, s in enumerate(steps)
                             if any(t in s.get("run", "") for t in
                                    ("gh pr create", "gh issue create"))
                             or "secrets." in json.dumps(s))
                self.assertLess(ids.index("revalidate"), actor)
                self.assertIn("grep -qxF -- \"$pair\" \"$RUNNER_TEMP/revalidate.out\"",
                              steps[-1]["run"])
                self.assertIn('"sha=${SHA}"', steps[-1]["run"])
        validate = self.jobs["validate"]["steps"]
        gate = next(s for s in validate if s.get("id") == "gate")
        self.assertIn("draft_new_skill.py validate", gate["run"])
        self.assertIn("--registry agentskills=../agentskills", gate["run"])

    def test_checkouts_are_default_branches(self):
        for name, job in self.jobs.items():
            for s in job["steps"]:
                if not str(s.get("uses", "")).startswith("actions/checkout@"):
                    continue
                with self.subTest(job=name, step=s["name"]):
                    w = s["with"]
                    if "repository" in w:
                        self.assertEqual(w["repository"], "example-adopter/agentskills")
                        self.assertNotIn("ref", w)
                    else:
                        self.assertEqual(w["ref"], "${{ github.event.repository.default_branch }}")

    def test_it_opens_two_pull_requests_and_one_issue_and_never_forces(self):
        bodies = "\n".join(body for _, body in run_blocks(self.doc))
        creates = re.findall(r"gh pr create[^\n]*(?:\\\n[^\n]*)*", bodies)
        self.assertEqual(len(creates), 2)
        for c in creates:
            self.assertIn('"${draft[@]}"', c)
        self.assertEqual(len(re.findall(r"gh issue create", bodies)), 1)
        issue = step(self.doc, "hook-issue", "Open the hook-proposal issue")["run"]
        self.assertIn('title="Hook proposal: ${SUBJECT}"', issue)
        self.assertIn("--label hook-proposal --label agent-authored", issue)
        self.assertIn("draft_new_skill.py issue-body", issue)
        for banned in ("--force", "push -f", "--admin", "gh pr review",
                       "--squash", "--rebase", "_agent-guidance"):
            self.assertNotIn(banned, bodies)
        targets = sorted(t for t in PUSH_TARGET.findall(bodies))
        self.assertEqual(targets, ['"HEAD:refs/heads/${target}"'] * 2)

    def test_the_merge_mode_is_the_improvement_gates_one_switch(self):
        """evals/improve-policy.yml via `improve_gate.py merge-mode`, decided
        before the token exists; a draft unless the mode is auto."""
        steps = self.jobs["skill-pr"]["steps"]
        ids = [s.get("id") for s in steps]
        mint = next(i for i, s in enumerate(steps)
                    if str(s.get("uses", "")).startswith("actions/create-github-app-token@"))
        self.assertLess(ids.index("mode"), mint)
        decide = steps[ids.index("mode")]
        self.assertIn("improve_gate.py merge-mode", decide["run"])
        self.assertIn("--tree ../agentskills", decide["run"])
        self.assertNotIn("--policy", decide["run"])
        self.assertNotIn("secrets.", json.dumps(decide))
        skill = step(self.doc, "skill-pr", "Open the skill's pull request in agentskills")
        self.assertEqual(skill["env"]["MODE"], "${{ steps.mode.outputs.mode }}")
        fixtures = step(self.doc, "fixtures-pr", "Open the fixtures' pull request")
        self.assertEqual(fixtures["env"]["AUTO"], "${{ needs.skill-pr.outputs.auto }}")
        self.assertEqual(self.jobs["skill-pr"]["outputs"]["auto"],
                         "${{ steps.open.outputs.auto }}")
        for run, mode in ((skill["run"], '"$MODE"'), (fixtures["run"], '"$mode"')):
            with self.subTest(mode=mode):
                self.assertIn(f"if [ {mode} = draft ]; then\n  draft=(--draft)\nfi",
                              run.replace("          ", ""))
                merges = re.findall(r"gh pr merge[^\n]*", run)
                self.assertEqual(len(merges), 1)
                self.assertTrue(merges[0].endswith("--auto --merge"))
                auto = run.index(f"if [ {mode} = auto ]; then\n", run.index("gh pr create"))
                self.assertLess(auto, run.index("gh pr merge"))
                self.assertLess(run.index("gh pr merge"), run.index('"MERGE"'))
        # The skill's is labeled before auto-merge and only then reports auto;
        # the fixtures follow it, never lead.
        run = skill["run"]
        self.assertLess(run.index("--add-label agent-authored > /dev/null \\"),
                        run.index("gh pr merge"))
        self.assertLess(run.index("gh pr merge"), run.index('echo "auto=true"'))
        self.assertIn("eval-improve/*|retire-skill/*|draft-skill/*)", run)
        self.assertIn('if [ "$AUTO" = true ]; then\n  mode=auto', fixtures["run"].replace("          ", ""))

    def test_the_fixtures_commit_is_evals_subject_only(self):
        body = step(self.doc, "fixtures-pr", "Open the fixtures' pull request")["run"]
        self.assertIn('target="draft-skill-fixtures/${SUBJECT}"', body)
        self.assertIn("draft_new_skill.py fixtures-apply", body)
        self.assertNotIn('git checkout -q "$SHA"', body, "files come from the staged copy")
        self.assertIn(':(exclude)evals/${SUBJECT}/', body)
        self.assertIn('":(exclude)README.md"', body)
        self.assertIn("--diff-filter=a", body)
        self.assertIn("--repo skills-evals", body)
        skill = step(self.doc, "skill-pr", "Open the skill's pull request in agentskills")["run"]
        self.assertIn('target="draft-skill/${SUBJECT}"', skill)
        self.assertIn("draft_new_skill.py apply", skill)
        # A bundle in the registry's skills.lock is re-pinned on a second
        # commit, and a missing relock line stops the job.
        self.assertIn('grep -qxF -- "relock=true"', skill)
        self.assertIn("python3 scripts/generate_skills_lock.py --repin", skill)
        self.assertIn('grep -qxF -- "relock=false"', skill)
        self.assertIn("--repo agentskills", skill)

    def test_the_guards_can_fail(self):
        self.assertIsNotNone(lock_problem("actions/checkout@v4", GATE))
        self.assertEqual(PUSH_TARGET.findall("git push origin HEAD:main"), ["HEAD:main"])
        self.assertTrue(fnmatch("claude/eval-x", "claude/eval-*"))


if __name__ == "__main__":
    unittest.main()
