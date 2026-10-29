#!/usr/bin/env python3
"""ADR 0005, "Routine guidance improve mode addendum": the guidance improve
loop end to end, with no human in it.

What is pinned here (the fire workflow's own guidance-improve checks are
test_issue_routine_eval_fire.py's, the planner's
test_issue_closed_loop_planner.py's):

  * improve_outcomes reads `Fire eval routine: guidance-improve <section>`
    fires and `claude/eval-guidance-improve-<run id>` branches as records
    keyed by `section`, and `charged_fires` is both modes;
  * the allowance guard counts, holds and checks a guidance-improve fire
    exactly as an improve fire, and its `--mode` accepts every mode the fire
    workflow passes (draft was refused by argparse until 2026-10-27);
  * scripts/guidance_improve_gate.py `validate` accepts a branch that adds
    exactly summary.json, report.md and guidance.patch under its run id, an
    accepted record whose split is the section's committed fixtures, and a
    patch of the row's file and the proposer's bookkeeping files; it
    rejects an extra file, a path outside that set, a rejected decision and
    a wrong branch name, staging nothing;
  * `prepare` builds the _agent-guidance commit with no credential, re-copies
    base.md to its fleet-memory payload, refuses a guidance that moved since
    measurement, and `allowed_change` is repo-settings ADR 0014's allowlist;
  * `open_pr`, against a fake gh: no token opens nothing and says why; `auto`
    opens a ready pull request with auto-merge (merge commit,
    `--match-head-commit`) read back; `draft` a draft; an open guidance-loop
    pull request opens nothing;
  * the record keys the gate allows cover what
    propose_guidance_edit.run_pipeline writes (read from its AST);
  * the two workflows, parsed with PyYAML: triggers, disjoint push filters,
    minimal permissions, the default-branch copy only, no `${{` in any
    `run:` block, no concurrency group, the App token minted for
    _agent-guidance alone under continue-on-error and reaching one step, and
    every `uses:` locked in actions.lock under the workflow's own entry.

Discovered and run by test/run_tests.py.
"""

from __future__ import annotations

import ast
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatch
from pathlib import Path
from unittest import mock

import yaml

TEST_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = TEST_DIR.parent
SCRIPTS = REPO_ROOT / "scripts"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
sys.path.insert(0, str(TEST_DIR))
import example_evals  # noqa: E402

#: The committed section fixtures these tests read: the example checkout's
#: (the kit ships none; test/example_evals.py).
EXAMPLE_EVALS = example_evals.checkout() / "evals"
GATE = WORKFLOWS / "routine-guidance-improve-gate.yml"
SIGNAL = WORKFLOWS / "routine-guidance-improve-pushed.yml"
IMPROVE_SIGNAL = WORKFLOWS / "routine-improve-pushed.yml"
RESULTS_SIGNAL = WORKFLOWS / "routine-eval-results-pushed.yml"
LOCK = WORKFLOWS / "actions.lock"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(TEST_DIR))

import allowance_guard as guard  # noqa: E402
import guidance_improve_gate as gate  # noqa: E402
import improve_outcomes as outcomes  # noqa: E402
import ingest_routine_results as ingest  # noqa: E402
from lockfile_pins import lock_problem  # noqa: E402

RUN_ID = "20261027T120000Z-0a1b2c"
BRANCH = f"claude/eval-guidance-improve-{RUN_ID}"
# The one committed section with the proposer's minimum of full-arm fixtures.
SECTION = "anything-you-name-gets-its-link"
FIXTURES = [f"guidance/{SECTION}/{n}"
            for n in ("done-summary", "handoff", "no-url", "waiting-on-review")]
HEADING = "Example section heading"
MARKER = "do-not-echo-this-marker"
NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
FORBIDDEN_IN_RUN = re.compile(r"\$\{\{")

GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.com",
    "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.com",
    "GIT_AUTHOR_DATE": "2026-10-27T00:00:00Z", "GIT_COMMITTER_DATE": "2026-10-27T00:00:00Z",
    "GIT_CONFIG_COUNT": "2",
    "GIT_CONFIG_KEY_0": "gc.auto", "GIT_CONFIG_VALUE_0": "0",
    "GIT_CONFIG_KEY_1": "maintenance.auto", "GIT_CONFIG_VALUE_1": "false",
}

BASE_MD = f"# Fleet guidance\n\n## {HEADING}\n\nAn invented rule.\n"
NEW_BASE_MD = f"# Fleet guidance\n\n## {HEADING}\n\nAn invented, sharper rule.\n"


def iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def fire(run_id: int, title: str, started: datetime, conclusion="success",
         status="completed") -> dict:
    return {"id": run_id, "display_title": title, "run_started_at": iso(started),
            "created_at": iso(started), "updated_at": iso(started + timedelta(minutes=5)),
            "conclusion": conclusion, "status": status}


def record(**changes) -> dict:
    doc = {
        "schema": 1, "section": SECTION, "timestamp": "20261027T120500Z",
        "local_exhibit": "skills-evals ADR 0002 decision 4",
        "row": {"file": "agents-md/base.md", "heading": HEADING, "status": "covered"},
        "guidance": {"path": "/tmp/agent-guidance", "sha": "1" * 40},
        "split": {"rotation": 0, "train": FIXTURES[:2], "validation": FIXTURES[2],
                  "holdout": FIXTURES[3]},
        "trials": {"initial": 3, "total": 3}, "no_judge": True,
        "models": {"arm": ["claude-opus-5-5"], "proposal": "claude-opus-5-5"},
        "proposal": {"model": "claude-opus-5-5", "rationale": "Sharper.",
                     "hook_candidates": []},
        "baseline": {}, "candidate": {}, "tokens_basis": "invented",
        "patch_validation": {"ok": True, "checks": {}}, "runs": "/tmp/runs",
        "files": {"patch": "/tmp/x.patch", "pr_body": "/tmp/x.pr-body.md"},
        "reasons": ["train improved; validation held; holdout held"],
        "table": "| fixture | before | after |", "status": "accepted", "exit_code": 0,
    }
    doc.update(changes)
    return doc


def diff(path: str, old: str, new: str) -> str:
    return (f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
            f"@@ -5 +5 @@\n-{old}\n+{new}\n")


def patch_text(path="agents-md/base.md") -> str:
    return diff(path, "An invented rule.", "An invented, sharper rule.")


def report_text(section=SECTION, extra="") -> str:
    return (f"## Proposed `{section}` guidance edit (validation-gated)\n\n"
            f"Produced by an invented run.{extra}\n")


def good_files(run_id=RUN_ID, **overrides) -> dict:
    root = f"{gate.SOURCE_ROOT}/{run_id}"
    files = {f"{root}/summary.json": json.dumps(record()).encode(),
             f"{root}/report.md": report_text().encode(),
             f"{root}/guidance.patch": patch_text().encode()}
    files.update({f"{root}/{k}": v for k, v in overrides.items()})
    return files


class OutcomeAndGuardTests(unittest.TestCase):
    """Records keyed by section; guard charges like an improve fire."""

    def runs(self):
        return [fire(1, f"Fire eval routine: guidance-improve {SECTION} ",
                     NOW - timedelta(hours=20)),
                fire(2, "Fire eval routine: improve writing-adrs ", NOW - timedelta(hours=3)),
                fire(3, f"Fire eval routine: guidance-improve {SECTION} ",
                     NOW - timedelta(minutes=30)),
                fire(4, "Fire eval routine: guidance-improve _delivery ",
                     NOW - timedelta(minutes=20)),
                fire(5, "Fire eval routine: eval evals/x claude-opus-5-5", NOW)]

    def test_guidance_fires_are_their_own_kind_keyed_by_section(self):
        fires = outcomes.improve_fires(self.runs(), "guidance-improve")
        self.assertEqual([(f["id"], f["section"]) for f in fires], [(1, SECTION), (3, SECTION)])
        self.assertTrue(all("skill" not in f for f in fires))
        self.assertEqual([f["id"] for f in outcomes.improve_fires(self.runs())], [2])
        self.assertEqual([f["id"] for f in outcomes.charged_fires(self.runs())], [1, 2, 3])

    def test_outcomes_match_branches_by_section(self):
        branch_id = (NOW - timedelta(hours=20) + timedelta(minutes=1)).strftime(
            "%Y%m%dT%H%M%SZ") + "-abcdef"
        records = outcomes.outcomes(self.runs(), [{"run_id": branch_id, "section": SECTION}],
                                    NOW, mode="guidance-improve")
        self.assertEqual([(r["section"], r["fire_run"], r["outcome"], r["run_id"])
                          for r in records],
                         [(SECTION, 1, "accepted", branch_id), (SECTION, 3, "pending", None)])
        # Another section's branch claims nothing.
        records = outcomes.outcomes(self.runs(), [{"run_id": branch_id, "section": "other"}],
                                    NOW, mode="guidance-improve")
        self.assertEqual(records[0]["outcome"], "rejected")

    def test_the_branch_fetch_reads_the_guidance_root(self):
        calls = []

        def get(path, token, raw=False, cap=None):
            calls.append(path)
            if "matching-refs" in path:
                return [{"ref": f"refs/heads/{BRANCH}"},
                        {"ref": "refs/heads/claude/eval-guidance-improve-bad"}]
            self.assertTrue(raw)
            return json.dumps({"section": SECTION}).encode()
        got = outcomes.fetch_improve_branches("example-adopter/skills-evals", "t", get,
                                              mode="guidance-improve")
        self.assertEqual(got, [{"run_id": RUN_ID, "section": SECTION}])
        self.assertIn("matching-refs/heads/claude/eval-guidance-improve-", calls[0])
        self.assertIn(f"contents/eval-guidance-improve/{RUN_ID}/summary.json", calls[1])

    def test_the_guard_counts_and_charges_guidance_fires_as_improve_fires(self):
        # Fires 1 to 3 in the week; only fire 3, a guidance fire, is inside
        # PENDING_FULL_HOLD (45 minutes).
        self.assertEqual(guard.improve_counts(self.runs(), NOW), (3, 1))
        self.assertEqual(len(guard.improve_starts(self.runs(), NOW)), 3)
        state = {"cap": 1.0, "spent": 0.0, "pending": 0.0, "pace_room": 0.0,
                 "improve_charge": 0.5, "reservation": 0.05, "runs": 0}
        # 0.4 left: eight eval reservations fit, one improve charge does not.
        self.assertTrue(guard.check({**state, "pending": 0.6}, "eval"))
        for mode in guard.IMPROVE_MODES:
            with self.subTest(mode=mode):
                self.assertTrue(guard.check(state, mode))
                self.assertFalse(guard.check({**state, "pending": 0.6}, mode))

    def test_every_fire_workflow_mode_is_a_guard_mode(self):
        # Regression: `draft` was missing, so argparse refused every draft
        # fire with exit 2 before the guard read anything.
        doc = yaml.safe_load((WORKFLOWS / "routine-eval-fire.yml").read_text())
        on = doc.get("on", doc.get(True))
        for mode in on["workflow_dispatch"]["inputs"]["mode"]["options"]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp, \
                    mock.patch.object(guard, "account", side_effect=guard.GuardError("x")), \
                    mock.patch("sys.stdout", new_callable=io.StringIO), \
                    mock.patch("sys.stderr", new_callable=io.StringIO) as err:
                code = guard.main(["check", "--results-tree", tmp, "--mode", mode],
                                  now=NOW, fire_count=lambda now: (0, 0),
                                  improve_count=lambda now: (0, 0))
                # Past argparse: the (failing) accounting decides, fail closed.
                self.assertEqual(code, 1, err.getvalue())
                self.assertNotIn("invalid choice", err.getvalue())


class GitCase(unittest.TestCase):
    """A scratch skills-evals with `main` and a guidance improve branch."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        env = mock.patch.dict(os.environ, GIT_ENV)
        env.start()
        self.addCleanup(env.stop)
        self.git("init", "-q", "-b", "main")
        (self.repo / "README.md").write_text("see https://example.com\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "main")
        self.git("checkout", "-q", "-b", BRANCH)

    def git(self, *args) -> str:
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self, files=None) -> str:
        for name, data in (good_files() if files is None else files).items():
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", "candidate")
        return self.git("rev-parse", "HEAD")

    def validate(self, branch=BRANCH, expect=None):
        return gate.validate(str(self.repo), "main", BRANCH, branch, expect,
                             self.tmp / "staged", evals_dir=EXAMPLE_EVALS)

    def assertRejected(self, pattern, **kwargs):
        with self.assertRaises(ingest.Rejected) as caught:
            self.validate(**kwargs)
        self.assertRegex(str(caught.exception), pattern)
        self.assertNotIn(MARKER, str(caught.exception))
        staged = self.tmp / "staged"
        self.assertFalse(staged.exists() and any(staged.iterdir()),
                         "a rejected branch stages nothing")


class ValidateTests(GitCase):

    def test_a_good_branch_is_accepted_and_staged_exactly(self):
        files = good_files()
        sha = self.commit(files)
        self.assertEqual(self.validate(expect=sha), {
            "run_id": RUN_ID, "sha": sha, "section": SECTION, "guidance_sha": "1" * 40,
            "section_file": "agents-md/base.md",
            "target_branch": f"eval-guidance-improve/{SECTION}"})
        for name, data in files.items():
            self.assertEqual((self.tmp / "staged" / Path(name).name).read_bytes(), data)

    def test_bookkeeping_files_may_ride_along(self):
        patch = patch_text() + diff("AGENTS.md", "An invented rule.", "An invented, "
                                    "sharper rule.") \
            + diff("docs/guidance-impact.md", "old", "new")
        self.commit(good_files(**{"guidance.patch": patch.encode()}))
        self.assertEqual(self.validate()["section"], SECTION)

    def test_an_extra_file_is_rejected(self):
        files = good_files()
        files[f"{gate.SOURCE_ROOT}/{RUN_ID}/notes.md"] = MARKER.encode()
        self.commit(files)
        self.assertRejected("not a file a guidance improve branch carries")
        self.git("reset", "-q", "--hard", "main")
        self.commit({**good_files(), "scripts/evil.py": MARKER.encode()})
        self.assertRejected("not under")

    def test_a_path_outside_the_allowlist_is_rejected(self):
        for path in (".github/workflows/ci.yml", ".claude/hooks/fleet-guidance.md",
                     "agents-md/sections/other.md", "scripts/sync.sh"):
            with self.subTest(path=path):
                self.git("reset", "-q", "--hard", "main")
                shutil.rmtree(self.tmp / "staged", ignore_errors=True)
                patch = patch_text() + diff(path, "a", MARKER)
                self.commit(good_files(**{"guidance.patch": patch.encode()}))
                self.assertRejected("outside the section's file")

    def test_a_rejected_or_refused_decision_is_rejected(self):
        for changes in ({"status": "rejected", "exit_code": 1},
                        {"status": "refused", "exit_code": 2},
                        {"patch_validation": {"ok": False, "checks": {}}}):
            with self.subTest(**{k: str(v) for k, v in changes.items()}):
                self.git("reset", "-q", "--hard", "main")
                self.commit(good_files(**{"summary.json":
                                          json.dumps(record(**changes)).encode()}))
                self.assertRejected("not accepted|did not pass")

    def test_a_split_that_is_not_the_committed_fixtures_is_rejected(self):
        split = {"rotation": 0, "train": FIXTURES[:1], "validation": FIXTURES[2],
                 "holdout": FIXTURES[3]}
        self.commit(good_files(**{"summary.json": json.dumps(record(split=split)).encode()}))
        self.assertRejected("not the section's fixtures")

    def test_a_wrong_branch_name_is_rejected(self):
        self.commit()
        for branch in (f"claude/eval-improve-{RUN_ID}", f"claude/eval-{RUN_ID}",
                       f"claude/eval-guidance-improve-{RUN_ID}x", "main"):
            with self.subTest(branch=branch):
                self.assertRejected("branch name is not", branch=branch)

    def test_the_wrong_run_id_root_and_a_moved_branch_are_rejected(self):
        self.commit(good_files(run_id="20261027T120000Z-ffffff"))
        self.assertRejected("not under")
        self.git("reset", "-q", "--hard", "main")
        self.commit()
        self.assertRejected("moved", expect="2" * 40)

    def test_a_report_with_a_closing_keyword_is_rejected(self):
        self.commit(good_files(**{"report.md": report_text(extra=" Closes #12").encode()}))
        self.assertRejected("closing keyword")


OTHER = "## Another section\n\nLeave this alone.\n"
IMPACT_OLD = ("# Guidance impact\n\nEntry format here.\n\n"
              "## 2026-10-01 — other-section — edit\n- Outcome: kept\n")


def entry(section=SECTION, date="2026-10-27") -> str:
    return (f"## {date} — {section} — edit\n- Motivation: invented\n"
            "- Outcome: pending\n\n")


def manifest(base_bytes: int, other_status="covered") -> str:
    return (f"- id: {SECTION}\n  heading: {HEADING}\n  file: agents-md/base.md\n"
            f"  status: covered\n  bytes: {base_bytes}\n"
            "- id: other-section\n  heading: Another section\n  file: agents-md/base.md\n"
            f"  status: {other_status}\n  bytes: 40\n")


class PrepareTests(unittest.TestCase):
    """The _agent-guidance commit, built with no credential, and what the
    applied patch may change."""

    def setUp(self):
        import propose_guidance_edit as proposer
        self.proposer = proposer
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        env = mock.patch.dict(os.environ, GIT_ENV)
        env.start()
        self.addCleanup(env.stop)
        self.tree = self.tmp / "agent-guidance"
        self.old_base = BASE_MD + "\n" + OTHER
        self.files = {
            "agents-md/base.md": self.old_base,
            "agents-md/eval-coverage.yml": manifest(self.size(self.old_base)),
            "docs/guidance-impact.md": IMPACT_OLD,
            "AGENTS.md": "# AGENTS.md\n",
            ".claude/hooks/fleet-guidance.md": self.old_base,
            "scripts/sync.sh": "#!/bin/sh\n",
        }
        self.write(self.files)
        self.git("init", "-q", "-b", "main")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "guidance")
        self.measured = self.git("rev-parse", "HEAD")
        self.staged = self.tmp / "staged"
        self.staged.mkdir()
        (self.staged / "summary.json").write_text(json.dumps(record(
            guidance={"path": "/tmp/x", "sha": self.measured})))
        (self.staged / "report.md").write_text(report_text())
        self.policy = self.tmp / "policy.yml"
        self.policy.write_text("improve_merge_mode: auto\n")
        self.new_base = NEW_BASE_MD + "\n" + OTHER
        self.honest = {
            "agents-md/base.md": self.new_base,
            "agents-md/eval-coverage.yml": manifest(self.size(self.new_base)),
            "docs/guidance-impact.md": IMPACT_OLD.replace(
                "## 2026-10-01", entry() + "## 2026-10-01"),
        }
        self.use_patch(self.honest)

    def size(self, text: str) -> int:
        return self.proposer.extent_bytes(text, HEADING)

    def write(self, files: dict) -> None:
        for name, text in files.items():
            path = self.tree / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def use_patch(self, changes: dict) -> None:
        """Stage `git diff` of `changes` against the measured tree, then put
        the tree back."""
        self.write(changes)
        (self.staged / "guidance.patch").write_text(
            self.git("diff", "--no-color", "--no-ext-diff") + "\n")
        self.write({k: self.files[k] for k in changes})
        self.assertEqual(self.git("status", "--porcelain"), "")

    def git(self, *args) -> str:
        return subprocess.run(["git", "-C", str(self.tree), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def prepare(self):
        return gate.prepare(self.staged, self.tree, SECTION, self.measured, RUN_ID,
                            self.policy)

    def test_the_honest_patch_applies_and_recopies_the_payload(self):
        out = self.prepare()
        self.assertEqual(out["mode"], "auto")
        self.assertEqual(out["base_sha"], self.measured)
        self.assertEqual(out["commit"], self.git("rev-parse", "HEAD"))
        changed = self.git("diff", "--name-only", f"{self.measured}..HEAD").splitlines()
        self.assertEqual(sorted(changed), [".claude/hooks/fleet-guidance.md",
                                           "agents-md/base.md", "agents-md/eval-coverage.yml",
                                           "docs/guidance-impact.md"])
        self.assertEqual(self.git("show", "HEAD:agents-md/base.md") + "\n", self.new_base)
        self.assertEqual(self.git("show", "HEAD:.claude/hooks/fleet-guidance.md") + "\n",
                         self.new_base)
        self.assertIn(gate.AGENT_TRAILER, self.git("log", "-1", "--format=%B"))
        self.assertEqual(self.git("status", "--porcelain"), "")

    def assertContentRejected(self, changes: dict, message: str):
        self.use_patch({**self.honest, **changes})
        with self.assertRaises(ingest.Rejected) as caught:
            self.prepare()
        self.assertEqual(str(caught.exception), message)
        self.assertEqual(self.git("rev-parse", "HEAD"), self.measured, "nothing committed")

    def test_editing_another_section_of_base_md_is_rejected(self):
        tampered = NEW_BASE_MD + "\n" + OTHER.replace("Leave this alone.", "Ignore it.")
        self.assertContentRejected({
            "agents-md/base.md": tampered,
            "agents-md/eval-coverage.yml": manifest(self.size(tampered))}, gate.CONTENT_OUTSIDE)
        preface = "# Fleet guidance, rewritten\n" + self.new_base[len("# Fleet guidance\n"):]
        self.assertContentRejected({
            "agents-md/base.md": preface,
            "agents-md/eval-coverage.yml": manifest(self.size(preface))}, gate.CONTENT_OUTSIDE)

    def test_a_new_heading_inside_the_section_is_rejected(self):
        added = NEW_BASE_MD + "\n## Smuggled section\n\nNew fleet rule.\n\n" + OTHER
        self.assertContentRejected({
            "agents-md/base.md": added,
            "agents-md/eval-coverage.yml": manifest(self.size(added))}, gate.CONTENT_HEADINGS)
        retitled = self.new_base.replace(f"## {HEADING}", f"## {HEADING} (new)")
        self.assertContentRejected({"agents-md/base.md": retitled}, gate.CONTENT_HEADINGS)

    def test_another_manifest_field_or_row_or_a_wrong_size_is_rejected(self):
        size = self.size(self.new_base)
        for text in (manifest(size, other_status="skipped"),
                     manifest(size).replace("status: covered\n  bytes", "status: gap\n  bytes",
                                            1),
                     manifest(size) + "- id: extra\n  heading: Extra\n  file: AGENTS.md\n",
                     manifest(size + 1),
                     manifest(self.size(self.old_base))):
            with self.subTest(text=text):
                self.assertContentRejected({"agents-md/eval-coverage.yml": text},
                                           gate.CONTENT_MANIFEST)

    def test_rewriting_the_impact_file_is_rejected(self):
        for text in (entry() + "# Guidance impact\n\nEntry format replaced.\n",
                     IMPACT_OLD.replace("- Outcome: kept", "- Outcome: reverted")
                     + "\n" + entry(),
                     IMPACT_OLD.replace("## 2026-10-01", entry("other-section")
                                        + "## 2026-10-01"),
                     IMPACT_OLD.replace("## 2026-10-01", entry() + entry()
                                        + "## 2026-10-01")):
            with self.subTest(text=text):
                self.assertContentRejected({"docs/guidance-impact.md": text},
                                           gate.CONTENT_IMPACT)

    def test_an_appended_impact_entry_is_accepted(self):
        self.use_patch({**self.honest, "docs/guidance-impact.md": IMPACT_OLD + "\n" + entry()})
        self.assertEqual(self.prepare()["mode"], "auto")

    def test_a_draft_policy_and_a_moved_guidance(self):
        self.policy.write_text("improve_merge_mode: draft\n")
        self.git("checkout", "-q", "-b", "scratch")
        (self.tree / "agents-md" / "eval-coverage.yml").write_text(
            manifest(self.size(self.old_base)) + "# moved\n")
        self.git("commit", "-q", "-am", "moved")
        with self.assertRaisesRegex(ingest.Rejected, "changed .* since it was measured"):
            self.prepare()
        self.git("checkout", "-q", "main")
        self.git("branch", "-q", "-D", "scratch")
        self.assertEqual(self.prepare()["mode"], "draft")

    def test_a_measured_sha_off_the_default_branch_is_refused(self):
        self.measured = "2" * 40
        with self.assertRaisesRegex(ingest.Rejected, "not on _agent-guidance"):
            self.prepare()

    def test_the_allowlist_is_adr_0014s(self):
        for path in ("agents-md/base.md", "agents-md/sections/x.md", "AGENTS.md",
                     "docs/guidance-impact.md", ".claude/hooks/fleet-guidance.md"):
            self.assertTrue(gate.allowed_change(self.tree, path), path)
        for path in ("scripts/sync.sh", ".github/workflows/ci.yml", "docs/other.md",
                     ".claude/settings.json"):
            self.assertFalse(gate.allowed_change(self.tree, path), path)
        (self.tree / ".claude/hooks/fleet-guidance.md").write_text("different\n")
        self.assertFalse(gate.allowed_change(self.tree, ".claude/hooks/fleet-guidance.md"))


class FakeGh:
    def __init__(self, open_heads=(), refs=(), method="MERGE"):
        self.calls, self.stdin = [], []
        self.open_heads, self.refs, self.method = open_heads, refs, method

    def __call__(self, args, stdin=None):
        self.calls.append(args)
        self.stdin.append(stdin)
        if args[:2] == ["api", "--paginate"]:
            return "".join(f"{h}\n" for h in self.open_heads)
        if "matching-refs" in " ".join(args):
            return "".join(f"{r}\n" for r in self.refs)
        if args[:2] == ["pr", "create"]:
            return f"https://github.com/{gate.GUIDANCE_REPO}/pull/41\n"
        if args[:2] == ["pr", "view"]:
            return self.method + "\n"
        return ""


class OpenPrTests(unittest.TestCase):

    def open(self, gh, token="t", mode="auto"):
        pushed = []
        out = gate.open_pr(token, gh, pushed.append, section=SECTION, run_id=RUN_ID,
                           base="main", commit="3" * 40, mode=mode, body="body")
        return out, pushed

    def test_no_token_opens_nothing_and_says_why(self):
        gh = FakeGh()
        out, pushed = self.open(gh, token=None)
        self.assertEqual(out, {"opened": "", "reason": gate.NO_TOKEN})
        self.assertEqual((gh.calls, pushed), ([], []))

    def test_auto_opens_ready_with_auto_merge_read_back(self):
        gh = FakeGh()
        out, pushed = self.open(gh)
        url = f"https://github.com/{gate.GUIDANCE_REPO}/pull/41"
        self.assertEqual(out, {"opened": url, "reason": ""})
        self.assertEqual(pushed, [f"eval-guidance-improve/{SECTION}"])
        create = next(c for c in gh.calls if c[:2] == ["pr", "create"])
        self.assertNotIn("--draft", create)
        self.assertEqual(create[create.index("--head") + 1], f"eval-guidance-improve/{SECTION}")
        self.assertIn(["api", "-X", "POST", f"repos/{gate.GUIDANCE_REPO}/issues/41/labels",
                       "-f", "labels[]=agent-authored"], gh.calls)
        merge = next(c for c in gh.calls if c[:2] == ["pr", "merge"])
        self.assertEqual(merge, ["pr", "merge", url, "-R", gate.GUIDANCE_REPO, "--auto",
                                 "--merge", "--match-head-commit", "3" * 40])
        self.assertFalse(any("--squash" in c or "--rebase" in c or "--admin" in c
                             for c in gh.calls))
        self.assertFalse(any("review" in c for c in gh.calls), "never approves")

    def test_auto_merge_that_did_not_take_is_a_failure(self):
        with self.assertRaisesRegex(ingest.Rejected, "auto-merge"):
            self.open(FakeGh(method="SQUASH"))

    def test_draft_opens_a_draft_and_arms_nothing(self):
        gh = FakeGh()
        out, _ = self.open(gh, mode="draft")
        self.assertTrue(out["opened"])
        create = next(c for c in gh.calls if c[:2] == ["pr", "create"])
        self.assertIn("--draft", create)
        self.assertFalse(any(c[:2] == ["pr", "merge"] for c in gh.calls))

    def test_an_open_loop_pr_or_existing_branch_opens_nothing(self):
        for gh in (FakeGh(open_heads=["retire-guidance/other"]),
                   FakeGh(open_heads=["eval-guidance-improve/other"]),
                   FakeGh(refs=[f"refs/heads/eval-guidance-improve/{SECTION}"])):
            with self.subTest(heads=gh.open_heads, refs=gh.refs):
                out, pushed = self.open(gh)
                self.assertEqual(out["opened"], "")
                self.assertTrue(out["reason"])
                self.assertEqual(pushed, [])
                self.assertFalse(any(c[:2] == ["pr", "create"] for c in gh.calls))
        out, _ = self.open(FakeGh(open_heads=["eval-improve/writing-adrs", "feature/x"]))
        self.assertTrue(out["opened"])

    def test_the_cli_with_no_token_exits_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            staged = Path(tmp)
            (staged / "report.md").write_text(report_text())
            out = io.StringIO()
            code = gate.main(["open-pr", "--staged", tmp, "--tree", tmp, "--section", SECTION,
                              "--run-id", RUN_ID, "--sha", "4" * 40, "--branch", BRANCH,
                              "--base", "main", "--commit", "3" * 40, "--mode", "auto"],
                             env={}, gh=FakeGh(), push=lambda b: None, out=out)
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue(), f"opened=\nreason={gate.NO_TOKEN}\n")

    def test_the_auto_body_says_it_merges_itself(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "report.md").write_text(report_text())
            body = gate.pr_body(Path(tmp), RUN_ID, "4" * 40, BRANCH, "auto")
            draft = gate.pr_body(Path(tmp), RUN_ID, "4" * 40, BRANCH, "draft")
        self.assertIn("merges itself", body)
        self.assertIn("nothing merges itself", draft)
        self.assertTrue(body.rstrip().endswith(gate.AGENT_MARKER))


class RecordKeysTests(unittest.TestCase):
    """The record keys the gate allows are the ones the proposer writes."""

    def test_run_pipeline_writes_only_allowed_keys(self):
        source = (SCRIPTS / "propose_guidance_edit.py").read_text(encoding="utf-8")
        fn = next(n for n in ast.walk(ast.parse(source))
                  if isinstance(n, ast.FunctionDef) and n.name == "run_pipeline")
        keys = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "record" for t in node.targets) \
                    and isinstance(node.value, ast.Dict):
                keys |= {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "update" and isinstance(node.func.value, ast.Name) \
                    and node.func.value.id == "record":
                keys |= {k.arg for k in node.keywords}
            if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) \
                    and node.value.id == "record" and isinstance(node.ctx, ast.Store) \
                    and isinstance(node.slice, ast.Constant):
                keys.add(node.slice.value)
        self.assertTrue(keys)
        self.assertLessEqual(keys, set(gate.RECORD_ALLOWED))
        self.assertLessEqual(set(gate.RECORD_REQUIRED), keys)


class FireCheckTests(unittest.TestCase):

    def test_the_committed_section_passes_and_others_do_not(self):
        gate.fire_check(SECTION, "", evals_dir=EXAMPLE_EVALS)
        gate.fire_check(SECTION, FIXTURES[1], evals_dir=EXAMPLE_EVALS)
        for section, holdout, message in (
                ("_delivery", "", gate.FIRE_BAD_SECTION),
                ("Upper", "", gate.FIRE_BAD_SECTION),
                ("no-such-section-xyz", "", gate.FIRE_TOO_FEW.format(n=4)),
                (SECTION, "handoff", gate.FIRE_BAD_HOLDOUT),
                (SECTION, f"guidance/other/{MARKER}", gate.FIRE_BAD_HOLDOUT)):
            with self.subTest(section=section, holdout=holdout):
                with self.assertRaises(ingest.Rejected) as caught:
                    gate.fire_check(section, holdout, evals_dir=EXAMPLE_EVALS)
                self.assertEqual(str(caught.exception), message)


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def triggers(doc: dict):
    return doc.get("on", doc.get(True))


def strings(node, path=()):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from strings(value, path + (key,))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from strings(value, path + (index,))
    elif isinstance(node, str):
        yield path, node


def uses_values(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "uses":
                yield value
            else:
                yield from uses_values(value)
    elif isinstance(node, list):
        for item in node:
            yield from uses_values(item)


class WorkflowShapeTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.gate = load(GATE)
        cls.signal = load(SIGNAL)
        cls.jobs = cls.gate["jobs"]

    def test_the_signal_is_a_filtered_push_with_no_reach(self):
        on = triggers(self.signal)
        self.assertEqual(set(on), {"push"})
        self.assertEqual(on["push"]["branches"], ["claude/eval-guidance-improve-*"])
        self.assertEqual(on["push"]["paths"], [f"{gate.SOURCE_ROOT}/**"])
        self.assertEqual(self.signal["permissions"], {})
        for job in self.signal["jobs"].values():
            self.assertNotIn("permissions", job)
            for step in job["steps"]:
                self.assertNotIn("uses", step, "the signal checks nothing out")

    def test_push_filters_keep_each_push_to_its_own_gate(self):
        signals = {"guidance": triggers(self.signal)["push"],
                   "improve": triggers(load(IMPROVE_SIGNAL))["push"],
                   "results": triggers(load(RESULTS_SIGNAL))["push"]}
        files = {"guidance": f"{gate.SOURCE_ROOT}/{RUN_ID}/summary.json",
                 "improve": f"eval-improve/{RUN_ID}/summary.json",
                 "results": f"{ingest.SOURCE_ROOT}/{RUN_ID}/x/report.md"}
        for name, push in signals.items():
            for other, path in files.items():
                with self.subTest(signal=name, file=other):
                    self.assertEqual(any(fnmatch(path, p) for p in push["paths"]),
                                     name == other)
        # The branch matches the results signal's broad filter, not improve's.
        self.assertTrue(any(fnmatch(BRANCH, b) for b in signals["results"]["branches"]))
        self.assertFalse(any(fnmatch(BRANCH, b) for b in signals["improve"]["branches"]))
        self.assertIsNone(ingest.BRANCH_RE.fullmatch(BRANCH))

    def test_no_other_pushed_signal_fires_on_a_guidance_improve_push(self):
        """Every routine-*-pushed.yml: none but this one both matches the
        branch and (when it filters on paths) the files the branch adds; and
        this one matches no other routine's branch."""
        ours = f"{gate.SOURCE_ROOT}/{RUN_ID}/summary.json"
        others = sorted(p for p in WORKFLOWS.glob("routine-*-pushed.yml") if p != SIGNAL)
        self.assertGreaterEqual(len(others), 4)
        for path in others:
            push = triggers(load(path))["push"]
            with self.subTest(workflow=path.name):
                branch_hit = any(fnmatch(BRANCH, b) for b in push["branches"])
                path_hit = "paths" not in push or any(fnmatch(ours, p) for p in push["paths"])
                self.assertFalse(branch_hit and path_hit)
        for branch in (f"claude/eval-{RUN_ID}", f"claude/eval-improve-{RUN_ID}",
                       f"claude/scaffold-{RUN_ID}", f"claude/draft-skill-{RUN_ID}"):
            self.assertFalse(any(fnmatch(branch, b)
                                 for b in triggers(self.signal)["push"]["branches"]))

    def test_the_gate_runs_on_workflow_run_of_the_signal(self):
        on = triggers(self.gate)
        self.assertEqual(set(on), {"workflow_run", "workflow_dispatch"})
        self.assertEqual(on["workflow_run"]["workflows"], [self.signal["name"]])
        self.assertEqual(on["workflow_run"]["types"], ["completed"])
        self.assertEqual(on["workflow_run"]["branches"], ["claude/eval-guidance-improve-*"])
        self.assertEqual(set(on["workflow_dispatch"]["inputs"]), {"branch"})

    def test_permissions_are_minimal_and_no_concurrency(self):
        self.assertEqual(self.gate["permissions"], {})
        self.assertEqual(set(self.jobs), {"validate", "guidance-pr"})
        for job in self.jobs.values():
            self.assertEqual(job["permissions"], {"contents": "read"})
            self.assertNotIn("concurrency", job)
            self.assertNotIn("env", job)
        for doc in (self.gate, self.signal):
            self.assertNotIn("concurrency", doc)
            self.assertNotIn("env", doc)
        self.assertEqual(self.jobs["guidance-pr"]["needs"], "validate")

    def test_only_the_default_branch_copy_runs(self):
        self.assertIn("github.ref == format('refs/heads/{0}', "
                      "github.event.repository.default_branch)",
                      self.jobs["validate"]["if"])
        for job in self.jobs.values():
            for step in job["steps"]:
                if str(step.get("uses", "")).startswith("actions/checkout@"):
                    self.assertEqual(step["with"]["ref"],
                                     "${{ github.event.repository.default_branch }}")
                    self.assertIs(step["with"]["persist-credentials"], False)

    def test_no_expression_in_any_run_block(self):
        for doc in (self.gate, self.signal):
            runs = [(p, v) for p, v in strings(doc) if p and p[-1] == "run"]
            self.assertTrue(runs)
            for path, value in runs:
                with self.subTest(path=path):
                    self.assertIsNone(FORBIDDEN_IN_RUN.search(value))

    def test_the_token_is_minted_for_agent_guidance_alone_and_reaches_one_step(self):
        steps = self.jobs["guidance-pr"]["steps"]
        names = [s.get("name") for s in steps]
        [mint] = [s for s in steps
                  if str(s.get("uses", "")).startswith("actions/create-github-app-token@")]
        self.assertIs(mint["continue-on-error"], True)
        self.assertEqual(mint["if"], "steps.credential.outputs.present == 'true'")
        self.assertEqual(mint["with"], {
            "client-id": "${{ secrets.EVAL_IMPROVE_APP_CLIENT_ID }}",
            "private-key": "${{ secrets.EVAL_IMPROVE_APP_PEM }}",
            "owner": "example-adopter", "repositories": "_agent-guidance",
            "permission-contents": "write", "permission-pull-requests": "write"})
        holders = [s.get("name") for s in steps
                   if "steps.guidance-token.outputs.token" in yaml.safe_dump(s)]
        self.assertEqual(holders, ["Open the pull request in _agent-guidance"])
        self.assertEqual(steps[names.index(holders[0])]["env"]["GUIDANCE_TOKEN"],
                         "${{ steps.guidance-token.outputs.token }}")
        # Built and re-validated before any credential exists.
        self.assertLess(names.index("Re-validate the candidate at the judged sha"),
                        names.index("Build the _agent-guidance commit"))
        self.assertLess(names.index("Build the _agent-guidance commit"), steps.index(mint))
        secrets = [p for p, v in strings(self.gate) if "secrets." in v]
        self.assertTrue(all(p[:2] == ("jobs", "guidance-pr") for p in secrets))
        self.assertNotIn("github.token", yaml.safe_dump(self.gate))

    def test_every_uses_is_locked_under_the_workflows_own_entry(self):
        lock = yaml.safe_load(LOCK.read_text(encoding="utf-8"))["workflows"]
        for path, doc in ((GATE, self.gate), (SIGNAL, self.signal)):
            uses = sorted(set(uses_values(doc)))
            with self.subTest(workflow=path.name):
                self.assertEqual(sorted(lock[f".github/workflows/{path.name}"]), uses)
                for value in uses:
                    self.assertIsNone(lock_problem(value, path))
        keys = list(lock)
        self.assertEqual(keys, sorted(keys))


if __name__ == "__main__":
    unittest.main()
