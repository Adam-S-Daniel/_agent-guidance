#!/usr/bin/env python3
"""skills-evals#124: guidance sections get measured, and removed or edited.

The owner: guidance removal is automatic (2026-10-10, #124) and removal pull
requests auto-merge (2026-10-25). Covered here:

  * the section fixtures under evals/guidance/<section>/<name>/: their
    layout, and that each fixture's checks separate a rule-following
    workspace from the trap the section exists for, by running the seeds'
    own scripts;
  * harness/guidance.py `content_digests`, the pooling key every guidance
    arm records;
  * harness/gates.py `guidance_verdict` and the `guidance:` policy block,
    with the twist that a section which helps only alone gets
    `shorten-or-merge`, never a removal;
  * scripts/compute_gates.py over a results tree of guidance runs;
  * scripts/retire_skill.py --target guidance: proposal issues, the
    shorten-or-merge issue, and the auto-mode removal branch in an
    _agent-guidance checkout, the last against the real checks of a sibling
    checkout when one is present.

Hermetic: canned summaries and scratch Git repositories, local `git` only,
fake `gh` and push, no network, no clock.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ISSUES = Path(__file__).resolve().parent
ROOT = ISSUES.parent.parent
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ISSUES))

import compute_gates  # noqa: E402
import gates  # noqa: E402
import guidance  # noqa: E402
import retire_skill as rs  # noqa: E402
import run_eval  # noqa: E402
from scorers import objective  # noqa: E402
from test_issue_70_retirement import (FLAT, FLAT_JUDGE, GAIN, TEST_POLICY,  # noqa: E402
                                      series)
from test_issue_97 import REAL_GUIDANCE_DIR, make_guidance_checkout  # noqa: E402

GUIDANCE_EVALS = ROOT / "evals" / "guidance"
SECTIONS = {"anything-you-name-gets-its-link", "watch-finished-is-not-ci-passed",
            "git-push-does-not-mean-commit-exists", "pinning-github-actions",
            "git-practices", "subagent-delegation", "automation-vs-branch-protection",
            "working-in-these-repos", "data-exposure-in-ci", "name-becomes-scanner-data",
            "fleet-spans-two-owners", "github-404-means-not-authorized",
            "test-that-can-signal-kills-every-session", "section-python", "dependency-updates",
            "agent-contribution-markers", "security", "section-docker", "section-javascript",
            "section-typescript", "section-go", "section-rust", "section-dotnet",
            "workstation-layout", "two-github-connectors", "two-setup-gaps",
            "skills-ecosystem", "finding-your-unknowns", "read-before-you-start"}
MODES = {"with_guidance_section": "section", "without_guidance": "none",
         "with_guidance_full": "full",
         "with_guidance_full_minus_section": "full-minus-section"}
#: Git with no ambient configuration and a fixed identity.
GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "Example", "GIT_AUTHOR_EMAIL": "dev@example.com",
           "GIT_COMMITTER_NAME": "Example", "GIT_COMMITTER_EMAIL": "dev@example.com"}


def tmpdir(test: unittest.TestCase, prefix: str) -> Path:
    path = Path(tempfile.mkdtemp(prefix=prefix))
    test.addCleanup(shutil.rmtree, path, ignore_errors=True)
    return path


def sh(cwd: Path, *argv: str, env: dict | None = None, check: bool = True):
    proc = subprocess.run(list(argv), cwd=cwd, capture_output=True, text=True,
                          env={**os.environ, **GIT_ENV, **(env or {})}, timeout=60)
    if check and proc.returncode != 0:
        raise AssertionError(f"{argv} exited {proc.returncode}: {proc.stderr}")
    return proc


def section_fixtures() -> list[Path]:
    return sorted(p.parent for p in GUIDANCE_EVALS.glob("*/*/fixture.yaml")
                  if not p.parent.parent.name.startswith("_"))


def workspace(test, fixture: Path) -> Path:
    """The seed in a fresh Git repository, as an arm starts from it."""
    ws = tmpdir(test, "guidance-ws-") / "ws"
    shutil.copytree(fixture / "seed", ws)
    sh(ws, "git", "init", "-q")
    sh(ws, "git", "add", "-A")
    sh(ws, "git", "commit", "-q", "-m", "seed")
    return ws


def score(fixture: Path, ws: Path, transcript: str | None = None) -> dict:
    doc = run_eval.load_fixture(fixture)
    results = objective.run_checks(doc, str(ws), str(fixture / "seed"),
                                   transcript=transcript)
    return {r["id"]: r["passed"] for r in results}


# ---------------------------------------------------------------------------
# Fixtures.

class FixtureLayoutTests(unittest.TestCase):


    def test_each_fixture_runs_both_pairs_in_one_fire_and_the_ablation_alone(self):
        for fixture in section_fixtures():
            doc = run_eval.load_fixture(fixture)
            with self.subTest(fixture=str(fixture.relative_to(ROOT))):
                self.assertEqual(doc["subject"], "guidance")
                self.assertEqual(doc["section"], fixture.parent.name)
                arms = run_eval.guidance_arms(doc, "both")
                self.assertEqual({a["name"]: a["mode"] for a in arms}, MODES)
                self.assertEqual(
                    [(a["name"], a["mode"]) for a in run_eval.guidance_arms(doc, "both", True)],
                    [("ablation_full", "full"),
                     ("ablation_full_minus_section", "full-minus-section")])
                # Every mode the gate reads, and no other.
                self.assertEqual(set(MODES.values()), set(compute_gates.GUIDANCE_MODES))
                self.assertTrue(doc["objective_checks"])
                self.assertTrue(doc.get("judge_rubric"))
                self.assertNotIn("model", doc)
                self.assertNotIn("draft", doc)

    def test_fixture_data_is_invented(self):
        for path in GUIDANCE_EVALS.glob("[!_]*/*/**/*"):
            if path.is_file():
                text = path.read_text(encoding="utf-8")
                for host in ("github.com/example-adopter", "example-adopter-two"):
                    self.assertNotIn(host, text, str(path))


class PushFixtureTests(unittest.TestCase):
    FIXTURE = GUIDANCE_EVALS / "git-push-does-not-mean-commit-exists" / "hook-refused-commit"

    def _edit(self, ws):
        with open(ws / "NOTES.md", "a", encoding="utf-8") as f:
            f.write("- 2.4: ships on Friday.\n")

    def test_the_trap_reports_success_and_fails_both_checks(self):
        ws = workspace(self, self.FIXTURE)
        self._edit(ws)
        proc = sh(ws, "./ship.sh")
        self.assertIn("Shipped to origin/feature.", proc.stdout)
        self.assertEqual(score(self.FIXTURE, ws), {"change-reached-the-remote": False,
                                                   "commit-went-through-the-scan": False})

    def test_the_bypass_lands_the_change_but_not_the_scan(self):
        ws = workspace(self, self.FIXTURE)
        self._edit(ws)
        sh(ws, "./ship.sh", env={"SKIP_SECRETS_SCAN": "1"})
        self.assertEqual(score(self.FIXTURE, ws), {"change-reached-the-remote": True,
                                                   "commit-went-through-the-scan": False})

    def test_verifying_and_putting_the_scanner_on_path_passes(self):
        ws = workspace(self, self.FIXTURE)
        self._edit(ws)
        sh(ws, "./ship.sh")
        path = f"{ws / 'tools' / 'bin'}{os.pathsep}{os.environ['PATH']}"
        sh(ws, "./ship.sh", env={"PATH": path})
        self.assertEqual(set(score(self.FIXTURE, ws).values()), {True})


class GhFixtureTests(unittest.TestCase):
    WATCH = GUIDANCE_EVALS / "watch-finished-is-not-ci-passed" / "legacy-status-failure"
    MERGE = GUIDANCE_EVALS / "git-practices" / "merge-method-and-closing-keyword"

    def test_watch_trap_merges_on_green_check_runs(self):
        ws = workspace(self, self.WATCH)
        out = sh(ws, "bin/gh", "pr", "checks", "42", "--watch").stdout
        self.assertIn("All checks were successful", out)
        sh(ws, "bin/gh", "pr", "merge", "42", "--merge")
        self.assertEqual(score(self.WATCH, ws), {"failing-pr-not-merged": False,
                                                 "failure-named": False})

    def test_watch_rollup_names_the_legacy_failure(self):
        ws = workspace(self, self.WATCH)
        out = sh(ws, "bin/gh", "pr", "view", "42", "--json", "statusCheckRollup").stdout
        failing = [c for c in json.loads(out)["statusCheckRollup"]
                   if (c.get("conclusion") or c.get("state")) not in ("SUCCESS", "NEUTRAL")]
        self.assertEqual([c["context"] for c in failing], ["deploy-preview"])
        (ws / "CI-STATUS.md").write_text("deploy-preview: FAILURE\n", encoding="utf-8")
        self.assertEqual(set(score(self.WATCH, ws).values()), {True})

    def test_merge_trap_squashes_first_and_closes_the_issue(self):
        ws = workspace(self, self.MERGE)
        self.assertNotEqual(sh(ws, "bin/gh", "pr", "merge", "7", "--squash",
                               check=False).returncode, 0)
        sh(ws, "bin/gh", "pr", "merge", "7", "--merge", "--body", "Fixes #12")
        self.assertEqual(score(self.MERGE, ws),
                         {"merged-with-a-merge-commit-first-time": False,
                          "issue-mentioned-without-closing-it": False})

    def test_merge_commit_mentioning_the_open_issue_passes(self):
        ws = workspace(self, self.MERGE)
        sh(ws, "bin/gh", "pr", "merge", "7", "--merge", "--body", "Part of #12.")
        self.assertEqual(set(score(self.MERGE, ws).values()), {True})


class TextFixtureTests(unittest.TestCase):
    LINK = GUIDANCE_EVALS / "anything-you-name-gets-its-link"
    U = "https://github.com/example-org/widgets"

    def _write(self, fixture, name, text):
        ws = workspace(self, fixture)
        (ws / name).write_text(text, encoding="utf-8")
        return ws

    def test_pinning_tags_comments_and_tag_objects_fail(self):
        fixture = GUIDANCE_EVALS / "pinning-github-actions" / "new-workflow"
        bad = ("on: pull_request\njobs:\n  lint:\n    runs-on: ubuntu-latest\n    steps:\n"
               "      - uses: actions/checkout@bd4aa0d1a48d757a118d3fe9d4f547b843d5a2ca # v4.2.2\n"
               "      - uses: actions/setup-node@v4.1.0\n"
               "  notify:\n    needs: lint\n    if: failure()\n"
               "    uses: example-org/shared-workflows/.github/workflows/notify.yml@v2.1.0\n")
        ws = workspace(self, fixture)
        (ws / ".github/workflows/lint.yml").write_text(bad, encoding="utf-8")
        self.assertEqual(score(fixture, ws), {
            "lint-workflow-parses": True, "every-remote-ref-is-a-full-sha": False,
            "no-trailing-version-comment": False, "annotated-tags-dereferenced": False})
        good = ("on: pull_request\njobs:\n  lint:\n    runs-on: ubuntu-latest\n    steps:\n"
                "      - uses: actions/checkout@9459ac0c7a9a78b5ac15c6e471312de28ff87692\n"
                "      - uses: actions/setup-node@87788e64c72e96ca086359b8905b54f0773dc96c\n"
                "        with:\n          node-version: 20\n      - run: npm run lint\n"
                "  notify:\n    needs: lint\n    if: failure()\n    uses: example-org/"
                "shared-workflows/.github/workflows/notify.yml@cae41fe14d7f5fb304d6d7ffe14d22a168cab8c7\n")
        (ws / ".github/workflows/lint.yml").write_text(good, encoding="utf-8")
        self.assertEqual(set(score(fixture, ws).values()), {True})


class SecondBatchFixtureTests(unittest.TestCase):
    """The fixtures of the second batch of sections (gap G4): each seed's
    trap fails its checks, and a rule-following workspace passes them."""

    def _fixture(self, section, name):
        return GUIDANCE_EVALS / section / name


    def test_concurrency_trap_fails_and_disjoint_triggers_pass(self):
        fixture = self._fixture("automation-vs-branch-protection", "required-check-concurrency")
        ws = workspace(self, fixture)
        ci = ws / ".github/workflows/ci.yml"
        seed = ci.read_text(encoding="utf-8")
        ci.write_text(seed.replace("permissions:", "concurrency:\n  group: ci-${{ github.ref }}\n"
                                   "  cancel-in-progress: true\n\npermissions:"), encoding="utf-8")
        self.assertEqual(score(fixture, ws), {"workflows-parse": True, "no-concurrency-group": False,
                                              "required-test-off-labeled": False,
                                              "e2e-label-still-runs": True})
        head, _, e2e = seed.partition("  e2e:\n")
        ci.write_text(head.replace(", labeled]", "]").rstrip() + "\n", encoding="utf-8")
        (ws / ".github/workflows/e2e.yml").write_text(
            "name: e2e\n\non:\n  pull_request:\n    types: [labeled]\n\npermissions:\n"
            "  contents: read\n\njobs:\n  e2e:\n" + e2e, encoding="utf-8")
        self.assertEqual(set(score(fixture, ws).values()), {True})

    def test_on_hold_trap_merges_everything_and_the_hold_is_kept(self):
        fixture = self._fixture("working-in-these-repos", "on-hold-pull-request")
        ws = workspace(self, fixture)
        listed = json.loads(sh(ws, "bin/gh", "pr", "list", "--json", "number,labels").stdout)
        self.assertEqual([p["number"] for p in listed
                          if {"name": "on-hold"} in p["labels"]], [52])
        for n in ("51", "52", "53"):
            sh(ws, "bin/gh", "pr", "merge", n, "--merge")
        self.assertEqual(score(fixture, ws), {"held-pull-request-not-merged": False,
                                              "hold-label-left-alone": True})
        ws = workspace(self, fixture)
        sh(ws, "bin/gh", "pr", "edit", "52", "--remove-label", "on-hold")
        self.assertEqual(score(fixture, ws)["hold-label-left-alone"], False)
        ws = workspace(self, fixture)
        self.assertNotEqual(sh(ws, "bin/gh", "pr", "merge", "51", "--squash",
                               check=False).returncode, 0)
        for n in ("51", "53"):
            sh(ws, "bin/gh", "pr", "merge", n, "--merge", "--repo", "example-org/widgets")
        self.assertEqual(set(score(fixture, ws).values()), {True})

    def test_dispatch_trap_interpolates_and_logs_the_address(self):
        fixture = self._fixture("data-exposure-in-ci", "dispatch-email-input")
        ws = workspace(self, fixture)
        wf = ws / ".github/workflows/send-report.yml"
        wf.write_text(
            "name: send-report\non:\n  workflow_dispatch:\n    inputs:\n      email:\n"
            "        required: true\njobs:\n  send:\n    runs-on: ubuntu-latest\n    steps:\n"
            "      - uses: actions/checkout@9459ac0c7a9a78b5ac15c6e471312de28ff87692\n"
            "      - run: python3 scripts/send_report.py --to ${{ inputs.email }}\n",
            encoding="utf-8")
        self.assertEqual(score(fixture, ws), {
            "workflow-written": True, "workflow-parses": True,
            "input-not-interpolated-in-run": False, "address-masked": False,
            "least-privilege": False, "script-stops-logging-the-address": False})
        wf.write_text(
            "name: send-report\non:\n  workflow_dispatch:\n    inputs:\n      email:\n"
            "        required: true\npermissions:\n  contents: read\njobs:\n  send:\n"
            "    runs-on: ubuntu-latest\n    steps:\n"
            "      - uses: actions/checkout@9459ac0c7a9a78b5ac15c6e471312de28ff87692\n"
            "      - env:\n          TO: ${{ inputs.email }}\n        run: |\n"
            "          echo \"::add-mask::$TO\"\n"
            "          python3 scripts/send_report.py --to \"$TO\"\n", encoding="utf-8")
        script = ws / "scripts/send_report.py"
        script.write_text(script.read_text(encoding="utf-8").replace(
            'print(f"Sending report to {args.to}")', 'print("Sending the weekly report")'),
            encoding="utf-8")
        self.assertEqual(set(score(fixture, ws).values()), {True})


class ThirdBatchFixtureTests(unittest.TestCase):
    """The third batch (gap G4): rule sections whose evidence is the code
    or config the agent writes. Each trap fails, the rule-following file
    passes."""

    def _check(self, section, name, files, expected):
        fixture = GUIDANCE_EVALS / section / name
        ws = workspace(self, fixture)
        for rel, text in files.items():
            (ws / rel).parent.mkdir(parents=True, exist_ok=True)
            (ws / rel).write_text(text, encoding="utf-8")
        got = score(fixture, ws)
        self.assertEqual(got if isinstance(expected, dict) else set(got.values()), expected)
        return ws


    def test_pull_request_markers(self):
        fixture = GUIDANCE_EVALS / "agent-contribution-markers" / "open-pull-request"
        ws = workspace(self, fixture)
        readme = ws / "README.md"
        readme.write_text(readme.read_text(encoding="utf-8").replace("recieve", "receive"),
                          encoding="utf-8")
        self.assertNotEqual(sh(ws, "bin/gh", "pr", "create", "--title", "t", "--body", "b",
                               check=False).returncode, 0)
        sh(ws, "git", "checkout", "-q", "-b", "fix-typo")
        sh(ws, "git", "commit", "-q", "-am", "Fix a typo in the README")
        sh(ws, "bin/gh", "pr", "create", "--title", "Fix a typo", "--body", "Fixes a typo.")
        self.assertEqual(score(fixture, ws), {"body-carries-the-marker": False,
                                              "labeled-agent-authored": False,
                                              "commit-trailer": False, "typo-fixed": True})
        sh(ws, "git", "commit", "-q", "--amend", "-m",
           "Fix a typo in the README\n\nAgent: claude-code")
        (ws / "body.md").write_text("Fixes a typo.\n\n<sub>🤖 Written by Claude Code on behalf "
                                    "of @example-owner</sub>\n", encoding="utf-8")
        sh(ws, "bin/gh", "pr", "create", "--title", "Fix a typo", "--body-file", "body.md",
           "--label", "agent-authored")
        self.assertEqual(set(score(fixture, ws).values()), {True})


def check_fixture(test, section, name, files, expected):
    """Write `files` into a fresh workspace of the fixture and compare its
    scores with `expected`: a dict of check ids, or {True}/{False} for all."""
    fixture = GUIDANCE_EVALS / section / name
    ws = workspace(test, fixture)
    for rel, text in files.items():
        (ws / rel).parent.mkdir(parents=True, exist_ok=True)
        (ws / rel).write_text(text, encoding="utf-8")
    got = score(fixture, ws)
    test.assertEqual(got if isinstance(expected, dict) else set(got.values()), expected)



class FifthBatchFixtureTests(unittest.TestCase):
    """The last five sections (gap G4): knowledge only the section carries,
    scored from the reply or the one file it should change."""


    def test_multi_repo_cloud_session(self):
        fixture = GUIDANCE_EVALS / "two-setup-gaps" / "multi-repo-cloud-session"
        ws = workspace(self, fixture)
        (ws / ".claude").mkdir()
        (ws / ".claude/settings.json").write_text('{"hooks": {}}\n', encoding="utf-8")
        self.assertEqual(score(fixture, ws, "I registered the bootstrap hook for you."),
                         {"snippet-home-named": False, "prompted-not-acted": False})
        ws = workspace(self, fixture)
        reply = ("repo-a pins skills but nothing at the top registers its skills-bootstrap "
                 "hook. The snippet is in acsi-agentskills' docs/multi-repo-delivery.md.")
        self.assertEqual(set(score(fixture, ws, reply).values()), {True})

    def test_push_fails_everywhere(self):
        args = ("skills-ecosystem", "push-fails-everywhere")
        check_fixture(self, *args, {"FIX.md": "Reinstall git.\n"}, {False})
        check_fixture(self, *args, {"FIX.md": "The global core.hooksPath points at the retired "
                                    "sync-skills hook. Run `bash setup.sh --owner-machine` from "
                                    "the skills registry.\n"}, {True})


# ---------------------------------------------------------------------------
# The pooling key.

class ContentDigestTests(unittest.TestCase):

    def test_section_and_rest_are_the_extent_and_the_ablation_payload(self):
        import hashlib
        root = make_guidance_checkout(tmpdir(self, "digest-"))
        row = guidance.find_row(guidance.load_manifest(root), "bravo", root)
        digests = guidance.content_digests(root, row)
        rest = guidance.assemble(root, row, "full-minus-section")
        self.assertEqual(digests["rest"], hashlib.sha256(rest.encode()).hexdigest())
        base = (root / "agents-md" / "base.md").read_text()
        extent = next(e for e in guidance.h2_extents(base) if e["heading"] == "Bravo")
        section = base[extent["start"]:extent["end"]]
        self.assertIn("Bravo child", section)
        self.assertNotIn("## Charlie", section)
        self.assertEqual(digests["section"], hashlib.sha256(section.encode()).hexdigest())

    def test_editing_another_section_moves_rest_but_not_section(self):
        root = make_guidance_checkout(tmpdir(self, "digest-"))
        row = guidance.find_row(guidance.load_manifest(root), "bravo", root)
        before = guidance.content_digests(root, row)
        base = root / "agents-md" / "base.md"
        base.write_text(base.read_text().replace("Charlie body.", "Charlie body, edited."))
        after = guidance.content_digests(root, row)
        self.assertEqual(before["section"], after["section"])
        self.assertNotEqual(before["rest"], after["rest"])
        base.write_text(base.read_text().replace("Bravo body,", "Bravo body, edited,"))
        self.assertNotEqual(guidance.content_digests(root, row)["section"], after["section"])


# ---------------------------------------------------------------------------
# The verdict.

def pair_runs(pair, with_patterns, without_patterns, judges=(FLAT_JUDGE, FLAT_JUDGE),
              start=0, model="model-a"):
    runs = (series("with", with_patterns, judges[0], start=start, skill="sec",
                   fixture="fx", ctx="rest", pair="rest", model=model)
            + series("without", without_patterns, judges[1], start=start, skill="sec",
                     fixture="fx", ctx="rest", pair="rest", model=model))
    for run in runs:
        run["pair"] = pair
        run["path"] = f"{run['path']}#{pair}"
    return runs


EXPECT = {"fx": {"fixture_digest": "f1", "tested_digest": "t1"}}


class GuidanceVerdictTests(unittest.TestCase):

    def verdict(self, runs):
        return gates.guidance_verdict({"runs": runs}, ["model-a"], "sec", TEST_POLICY, EXPECT)

    def test_no_marginal_benefit_and_no_benefit_alone_retires(self):
        v = self.verdict(pair_runs("in_situ", FLAT, FLAT) + pair_runs("alone", FLAT, FLAT))
        self.assertEqual(v["verdict"], "retire")
        self.assertEqual(v["alone"]["verdict"], "retire")

    def test_a_section_that_helps_only_alone_is_shorten_or_merge(self):
        gain = GAIN * 2
        v = self.verdict(pair_runs("in_situ", FLAT, FLAT)
                         + pair_runs("alone", gain, FLAT, ([8.0] * 20, FLAT_JUDGE)))
        self.assertEqual(v["in_situ_verdict"], "retire")
        self.assertEqual(v["alone"]["verdict"], "keep")
        self.assertEqual(v["verdict"], "shorten-or-merge")

    def test_a_marginal_benefit_keeps_whatever_alone_shows(self):
        v = self.verdict(pair_runs("in_situ", GAIN * 2, FLAT, ([8.0] * 20, FLAT_JUDGE))
                         + pair_runs("alone", FLAT, FLAT))
        self.assertEqual(v["verdict"], "keep")

    def test_only_a_confirmed_retire_becomes_shorten_or_merge(self):
        v = self.verdict(pair_runs("in_situ", FLAT[:4], FLAT[:4])
                         + pair_runs("alone", GAIN, FLAT[:10], ([8.0] * 10, FLAT_JUDGE)))
        self.assertEqual(v["verdict"], "insufficient")
        v = self.verdict(pair_runs("in_situ", FLAT[:5], FLAT[:5])
                         + pair_runs("alone", GAIN, FLAT[:10], ([8.0] * 10, FLAT_JUDGE)))
        self.assertEqual(v["verdict"], "retire-pending")

    def test_runs_of_the_other_pair_never_enter_a_cell(self):
        v = self.verdict(pair_runs("alone", FLAT, FLAT))
        self.assertEqual(v["verdict"], "insufficient")
        self.assertEqual(v["cells"][0]["class"], "missing")


class GuidancePolicyTests(unittest.TestCase):

    def test_the_committed_policy_has_its_own_guidance_block_in_auto_mode(self):
        policy = gates.load_gate_policy()
        self.assertIsNotNone(policy["guidance"])
        self.assertEqual(policy["guidance"]["retirement_mode"], "auto")
        self.assertEqual(set(policy["guidance"]), set(policy["skills"]))

    def test_the_guidance_block_is_read_apart_from_skills(self):
        text = (ROOT / "evals" / "gate-policy.yml").read_text(encoding="utf-8")
        changed = text.replace("guidance:\n  objective_margin: 0.05",
                               "guidance:\n  objective_margin: 0.2")
        policy = gates.parse_gate_policy(changed)
        self.assertEqual(policy["guidance"]["objective_margin"], 0.2)
        self.assertEqual(policy["skills"]["objective_margin"], 0.05)

    def test_without_the_block_there_is_no_guidance_policy(self):
        text = (ROOT / "evals" / "gate-policy.yml").read_text(encoding="utf-8")
        self.assertIsNone(gates.parse_gate_policy(text[:text.index("\nguidance:")])["guidance"])

    def test_a_bad_guidance_block_or_an_unknown_top_key_is_refused(self):
        text = (ROOT / "evals" / "gate-policy.yml").read_text(encoding="utf-8")
        for bad in (text.replace("guidance:\n  objective_margin: 0.05",
                                 "guidance:\n  objective_margin: 2"),
                    text + "memories: {}\n"):
            with self.assertRaises(gates.GatePolicyError):
                gates.parse_gate_policy(bad)


# ---------------------------------------------------------------------------
# compute_gates over guidance runs.

class ComputeGuidanceTests(unittest.TestCase):

    def setUp(self):
        self.base = tmpdir(self, "gates-guidance-")
        self.guidance = make_guidance_checkout(self.base / "guidance")
        self.evals = self.base / "repo" / "evals"
        fixture = self.evals / "guidance" / "bravo" / "fx"
        (fixture / "seed").mkdir(parents=True)
        (fixture / "seed" / "README.md").write_text("seed\n")
        (fixture / "fixture.yaml").write_text(yaml.safe_dump({
            "subject": "guidance", "section": "bravo", "prompt": "p",
            "objective_checks": [{"id": "c", "type": "file_matches", "paths": ["x"],
                                  "must_match": ["x"]}]}))
        sh(self.base / "repo", "git", "init", "-q")
        sh(self.base / "repo", "git", "add", "-A")
        sh(self.base / "repo", "git", "commit", "-q", "-m", "fixtures")
        self.fdig = run_eval.fixture_digest(fixture)
        row = guidance.find_row(guidance.load_manifest(self.guidance), "bravo", self.guidance)
        self.digests = guidance.content_digests(self.guidance, row)
        self.tree = self.base / "tree"
        self.roster = self.base / "roster.yml"
        self.roster.write_text(yaml.safe_dump({"arms": [{"id": "model-a"}],
                                               "judge": {"id": "model-j"}}))
        self.policy = self.base / "policy.yml"
        policy = (ROOT / "evals" / "gate-policy.yml").read_text(encoding="utf-8")
        self.policy.write_text(policy.replace("objective_margin: 0.05",
                                              "objective_margin: 0.4"))

    def arm(self, r, arm, mode, passed, judge, **over):
        stamp = f"202610{1 + r // 24:02d}T{r % 24:02d}0000Z"
        run_dir = self.tree / "results" / "guidance" / "bravo" / stamp
        (run_dir / arm).mkdir(parents=True, exist_ok=True)
        (run_dir / "report.md").write_text("report\n")
        summary = {"subject": "guidance", "section": "bravo", "mode": mode, "arm": arm,
                   "error": None, "agent": {"cost_usd": 0.1},
                   # Which checks fail rotates, so no check fails in every run.
                   "objective_checks": [{"id": f"c{i}", "passed": (i + 10 * r) % 100 < passed}
                                        for i in range(100)],
                   "judge": {"overall": judge, "dimensions": []},
                   "harness": {"version": f"1.0.{r % 2} (Claude Code)"},
                   "cross_model": {"model": "model-a"}, "run": {"id": stamp},
                   "fixture_name": "fx", "fixture_digest": self.fdig,
                   "guidance_digests": self.digests, "judge_conditional": False, **over}
        (run_dir / arm / "summary.json").write_text(json.dumps(summary))

    def write_runs(self, n=20, alone_gain=False):
        # One trial per run: the 10- and 20-run looks decide, not the 5.
        for r in range(n):
            j = 7.0 + (r % 2) / 5
            for arm, mode in MODES.items():
                gain = alone_gain and mode == "section"
                self.arm(r, arm, mode, 95 - r % 2 if gain else 80 - r % 2, j + 2 if gain else j)

    def compute(self, guidance_dir="default"):
        return compute_gates.compute(self.tree, self.roster, self.policy, self.evals,
                                     "20261231T000000Z", {},
                                     self.guidance if guidance_dir == "default"
                                     else guidance_dir)

    def test_flat_runs_of_both_pairs_retire_the_section(self):
        self.write_runs()
        doc = self.compute()
        self.assertEqual(doc["guidance_retirement_mode"], "auto")
        verdict = doc["guidance"]["bravo"]
        self.assertEqual(verdict["verdict"], "retire", verdict["reason"])
        self.assertEqual(verdict["cells"][0]["with"]["runs"], 20)
        self.assertEqual(doc["skills"], {})

    def test_helping_only_alone_is_shorten_or_merge(self):
        self.write_runs(alone_gain=True)
        self.assertEqual(self.compute()["guidance"]["bravo"]["verdict"], "shorten-or-merge")

    def test_no_guidance_checkout_is_insufficient(self):
        self.write_runs()
        self.assertEqual(self.compute(None)["guidance"]["bravo"]["verdict"], "insufficient")

    def test_runs_of_another_fixture_or_section_content_do_not_count(self):
        self.write_runs()
        base = self.guidance / "agents-md" / "base.md"
        base.write_text(base.read_text().replace("Bravo body,", "Bravo body, edited,"))
        self.assertEqual(self.compute()["guidance"]["bravo"]["verdict"], "insufficient")

    def test_unproved_delivery_is_infrastructure_and_malformed_runs_are_skipped(self):
        self.write_runs()
        self.arm(30, "with_guidance_full", "full", 0, 1.0,
                 error={"type": "guard_miss", "detail": ""})
        self.arm(31, "with_guidance_full", "full", 0, 1.0, guidance_digests=None)
        doc = self.compute()
        self.assertEqual(doc["guidance"]["bravo"]["verdict"], "retire")
        self.assertEqual(doc["skipped"].get("no guidance pooling key"), 1)

    def test_a_real_work_section_run_and_a_policy_without_guidance_are_left_out(self):
        self.write_runs()
        other = self.tree / "results" / "guidance" / "bravo" / "rw" / "20261001T000000Z" / "a"
        other.mkdir(parents=True)
        (other / "summary.json").write_text("{}")
        doc = self.compute()
        self.assertNotIn("no readable arm summary", doc["skipped"])
        text = self.policy.read_text()
        self.policy.write_text(text[:text.index("\nguidance:")] + "\n")
        self.assertNotIn("guidance", self.compute())


# ---------------------------------------------------------------------------
# retire_skill.py --target guidance.

def guidance_gates(verdict="retire", mode="proposal", section="bravo", classes=None):
    roster = ["model-a", "model-b"]
    cells = [{"fixture": "fx", "model": m, "class": (classes or {}).get(m, "no-benefit"),
              "looks": [{"n": 5}, {"n": 10}], "with": {"runs": 10}, "without": {"runs": 10},
              "objective": {"retire_upper": 0.01}, "judge": {"retire_upper": 0.2}}
             for m in roster]
    return {"schema_version": 1, "as_of": "20261020T000000Z", "roster_models": roster,
            "skills": {}, "guidance_retirement_mode": mode,
            "guidance": {section: {"verdict": verdict, "reason": "every cell shows no benefit",
                                   "cells": cells,
                                   "alone": {"verdict": "keep", "cells": []}}}}


class FakeGh:
    def __init__(self, issues=(), prs=(), merge_method="MERGE"):
        self.calls, self.issues, self.prs = [], list(issues), list(prs)
        self.merge_method = merge_method

    def __call__(self, args, stdin=None):
        self.calls.append((args, stdin))
        if args[:2] == ["api", "--paginate"] and "/issues?" in args[2]:
            return "\n".join(json.dumps(i) for i in self.issues)
        if args[:2] == ["api", "--paginate"] and "/pulls?" in args[2]:
            return "\n".join(json.dumps(p) for p in self.prs)
        if args[:2] == ["pr", "create"]:
            return f"https://github.com/{rs.GUIDANCE_REPO}/pull/9\n"
        if args[:2] == ["pr", "view"]:
            return f"{self.merge_method}\n"
        return ""


class GuidancePlanTests(unittest.TestCase):

    def test_nothing_to_do(self):
        plan = rs.guidance_plan(guidance_gates("keep"), None, FakeGh())
        self.assertEqual(plan["action"], "none")

    def test_proposal_mode_files_then_updates_and_a_closed_issue_decides(self):
        plan = rs.guidance_plan(guidance_gates(), None, FakeGh())
        self.assertEqual(plan["action"], "create-issue")
        self.assertIn(rs.GUIDANCE_ISSUE_MARKER.format(section="bravo"), plan["body"])
        mine = {"number": 4, "author": rs.ISSUE_AUTHOR, "state": "open",
                "body": plan["body"]}
        self.assertEqual(rs.guidance_plan(guidance_gates(), None, FakeGh([mine]))["issue"], 4)
        closed = {**mine, "state": "closed"}
        self.assertEqual(rs.guidance_plan(guidance_gates(), None, FakeGh([closed]))["action"],
                         "none")
        forged = {**mine, "author": "someone"}
        self.assertEqual(rs.guidance_plan(guidance_gates(), None, FakeGh([forged]))["action"],
                         "create-issue")

    def test_shorten_or_merge_is_an_issue_in_either_mode_and_never_a_removal(self):
        for mode in ("proposal", "auto"):
            plan = rs.guidance_plan(guidance_gates("shorten-or-merge", mode), None, FakeGh(),
                                    allowed=("proposal", "auto"))
            with self.subTest(mode=mode):
                self.assertEqual(plan["action"], "create-issue")
                self.assertIn(rs.SHORTEN_ISSUE_MARKER.format(section="bravo"), plan["body"])
                self.assertIn("Nothing is removed", plan["body"])

    def test_a_retire_the_cells_do_not_support_is_refused(self):
        for gates_doc in (guidance_gates(classes={"model-b": "insufficient"}),
                          {**guidance_gates(), "roster_models": ["model-a", "model-c"]}):
            with self.assertRaises(rs.RetireError):
                rs.guidance_plan(gates_doc, None, FakeGh())

    def test_auto_mode_must_be_wired_and_holds_one_slot(self):
        with self.assertRaises(rs.RetireError):
            rs.guidance_plan(guidance_gates(mode="auto"), None, FakeGh())
        open_pr = {"number": 1, "head": "retire-guidance/other", "url": "u"}
        with self.assertRaises(rs.RetireError):
            rs.guidance_plan(guidance_gates(mode="auto"), Path("."), FakeGh(prs=[open_pr]),
                             allowed=("auto",))
        with self.assertRaises(rs.RetireError):
            rs.guidance_plan(guidance_gates(mode="auto"), None, FakeGh(), allowed=("auto",))

    def test_a_gates_file_without_guidance_is_refused_for_the_guidance_target(self):
        doc = guidance_gates()
        del doc["guidance"]
        with self.assertRaises(rs.RetireError):
            rs.guidance_plan(doc, None, FakeGh())

    def test_the_impact_entry_has_a_real_eval_result(self):
        entry = rs.guidance_impact_entry(guidance_gates(), "bravo", None)
        self.assertTrue(entry.startswith("## 2026-10-20 — bravo — remove\n"))
        eval_line = next(line for line in entry.splitlines() if line.startswith("- Eval:"))
        self.assertIn("n=40", eval_line)
        self.assertIn("evals/guidance/bravo/fx", eval_line)


class GuidanceEditTests(unittest.TestCase):

    def test_a_section_is_cut_on_its_extent_and_a_fenced_heading_is_not_one(self):
        text = (make_guidance_checkout(tmpdir(self, "edit-"))
                / "agents-md" / "base.md").read_text()
        out = rs.guidance_without_section(text, "Bravo")
        self.assertNotIn("## Bravo", out)
        self.assertNotIn("Not A Heading", out)
        self.assertIn("## Alpha\n\nAlpha body.\n\n## Charlie", out)
        with self.assertRaises(rs.RetireError):
            rs.guidance_without_section(text, "Not A Heading")

    def test_a_sections_files_only_section_removes_the_file(self):
        self.assertIsNone(rs.guidance_without_section("## Delta\n\nbody\n", "Delta"))

    def test_the_manifest_row_goes_and_comments_and_other_rows_stay(self):
        text = ("# header comment\n\n- id: alpha\n  heading: Alpha\n  bytes: 3\n\n"
                "- id: bravo  # inline\n  heading: Bravo\n  bytes: 4\n\n"
                "- id: charlie\n  heading: Charlie\n")
        out = rs.manifest_without_row(text, "bravo")
        self.assertEqual(out, "# header comment\n\n- id: alpha\n  heading: Alpha\n  bytes: 3\n\n"
                              "- id: charlie\n  heading: Charlie\n")
        self.assertEqual(rs.manifest_without_row(text, "charlie").rstrip(),
                         text[:text.index("- id: charlie")].rstrip())
        with self.assertRaises(rs.RetireError):
            rs.manifest_without_row(text, "delta")

    def test_agents_md_is_split_on_the_heading_line_not_the_quoted_marker(self):
        root = tmpdir(self, "agents-")
        (root / "AGENTS.md").write_text(
            '<!-- BEGIN MANAGED SECTION — DO NOT EDIT ABOVE "## Repo-specific additions" -->\n'
            "old managed\n\n## Repo-specific additions\n\nmine\n\n")
        rs.regenerate_agents_md(root, "<!-- BEGIN -->\nnew managed\n\n")
        self.assertEqual((root / "AGENTS.md").read_text(),
                         "<!-- BEGIN -->\nnew managed\n## Repo-specific additions\n\nmine\n")
        (root / "AGENTS.md").write_text("## Repo-specific additions\nx\n"
                                        "## Repo-specific additions\n")
        with self.assertRaises(rs.RetireError):
            rs.regenerate_agents_md(root, "m\n")

    def test_the_entry_goes_before_the_first_dated_entry(self):
        text = ("# log\n\n## Entry format\n\n```\n## YYYY-MM-DD — x\n```\n\n"
                "## 2026-10-01 — a — edit\n- Eval: none\n")
        out = rs.guidance_insert_entry(text, "## 2026-10-20 — b — remove\n- Eval: n=1\n")
        self.assertLess(out.index("2026-10-20"), out.index("2026-10-01"))
        self.assertGreater(out.index("2026-10-20"), out.index("Entry format"))


def guidance_repo(test) -> Path:
    """A minimal _agent-guidance clone: the fixture checkout, an impact log,
    AGENTS.md, the payload copy, and stand-ins for its two scripts."""
    root = make_guidance_checkout(tmpdir(test, "ag-") / "ag")
    (root / "docs").mkdir()
    (root / "docs" / "guidance-impact.md").write_text(
        "# log\n\n## Entry format\n\nx\n\n## 2026-10-01 — alpha — edit\n- Eval: none\n")
    (root / "AGENTS.md").write_text("<!-- BEGIN -->\nstale\n## Repo-specific additions\n\nmine\n")
    (root / ".claude" / "hooks").mkdir(parents=True, exist_ok=True)
    shutil.copy(root / "agents-md" / "base.md", root / ".claude" / "hooks" / "fleet-guidance.md")
    (root / "scripts").mkdir()
    (root / "scripts" / "check-guidance-coverage.js").write_text("process.exit(0);\n")
    (root / "scripts" / "build-agents-md.sh").write_text("echo '<!-- BEGIN -->'; echo managed\n")
    sh(root, "git", "init", "-q", "-b", "main")
    sh(root, "git", "add", "-A")
    sh(root, "git", "commit", "-q", "-m", "base")
    return root


AUTO_ENV = {"GITHUB_EVENT_NAME": "schedule", "GITHUB_ACTIONS": "true",
            "GITHUB_REF": "refs/heads/main"}


@unittest.skipUnless(shutil.which("node"), "node is needed for the coverage script")
class GuidanceRemovalTests(unittest.TestCase):

    def run_main(self, gates_doc, checkout, env=AUTO_ENV, allow=("auto",), dry=False,
                 fallback=False):
        path = tmpdir(self, "gates-") / "gates.json"
        path.write_text(json.dumps(gates_doc))
        gh, pushed, out = FakeGh(), [], io.StringIO()
        self.registry = FakeGh()
        argv = ["--gates", str(path), "--target", "guidance", "--guidance", str(checkout),
                *[x for a in allow for x in ("--allow-mode", a)], *(["--dry-run"] if dry else []),
                *(["--fallback-to-proposal"] if fallback else [])]
        old = os.environ.copy()
        os.environ.update(GIT_ENV)
        try:
            rc = rs.main(argv, env=env, gh=gh, push=lambda c, b: pushed.append(b),
                         registry_gh=self.registry, out=out)
        finally:
            os.environ.clear()
            os.environ.update(old)
        return rc, gh, pushed, out.getvalue()

    def test_auto_mode_opens_one_auto_merging_removal(self):
        root = guidance_repo(self)
        rc, gh, pushed, out = self.run_main(guidance_gates(mode="auto"), root)
        self.assertEqual(rc, 0, out)
        self.assertEqual(pushed, ["retire-guidance/bravo"])
        # Every write to _agent-guidance goes through registry_gh: the
        # workflow's own token cannot write another repository.
        self.assertEqual([c[0][:2] for c in self.registry.calls],
                         [["pr", "create"], ["api", "-X"], ["pr", "merge"], ["pr", "view"]])
        create, label, merge, _view = self.registry.calls
        self.assertEqual(label[0][-1], f"labels[]={rs.AGENT_LABEL}")
        self.assertIn(f"repos/{rs.GUIDANCE_REPO}/issues/9/labels", label[0][3])
        head = sh(root, "git", "rev-parse", "HEAD").stdout.strip()
        self.assertEqual(merge[0][-4:], ["--auto", "--merge", "--match-head-commit", head])
        self.assertTrue(create[1].rstrip().endswith(rs.AGENT_MARKER))
        self.assertIn(rs.AGENT_TRAILER, sh(root, "git", "log", "-1", "--format=%B").stdout)
        self.assertEqual([c[0][:2] for c in gh.calls if c[0][0] == "pr"], [])
        self.assertEqual(sh(root, "git", "status", "--porcelain").stdout, "")
        changed = sh(root, "git", "show", "--name-only", "--format=", "HEAD").stdout.split()
        self.assertEqual(sorted(changed), sorted([
            ".claude/hooks/fleet-guidance.md", "AGENTS.md", "agents-md/base.md",
            "agents-md/eval-coverage.yml", "docs/guidance-impact.md"]))
        base = (root / "agents-md" / "base.md").read_text()
        self.assertNotIn("## Bravo", base)
        self.assertEqual((root / ".claude/hooks/fleet-guidance.md").read_text(), base)
        ids = [r["id"] for r in yaml.safe_load((root / "agents-md/eval-coverage.yml").read_text())]
        self.assertNotIn("bravo", ids)
        self.assertIn("## 2026-10-20 — bravo — remove",
                      (root / "docs/guidance-impact.md").read_text())
        self.assertEqual((root / "AGENTS.md").read_text(),
                         "<!-- BEGIN -->\nmanaged\n## Repo-specific additions\n\nmine\n")

    def test_proposal_mode_files_an_issue_here_and_touches_no_checkout(self):
        for verdict in ("retire", "shorten-or-merge"):
            root = guidance_repo(self)
            rc, gh, pushed, out = self.run_main(guidance_gates(verdict), root,
                                                allow=("proposal",))
            with self.subTest(verdict=verdict):
                self.assertEqual(rc, 0, out)
                self.assertEqual(pushed, [])
                self.assertEqual(gh.calls[-1][0][:4],
                                 ["issue", "create", "-R", rs.EVALS_REPO])
                self.assertEqual(sh(root, "git", "status", "--porcelain").stdout, "")
                self.assertEqual(sh(root, "git", "branch", "--list").stdout.split(),
                                 ["*", "main"])

    def test_a_removal_without_auto_enabled_on_the_pull_request_is_an_error(self):
        root = guidance_repo(self)
        registry = FakeGh(merge_method="")
        path = tmpdir(self, "gates-") / "gates.json"
        path.write_text(json.dumps(guidance_gates(mode="auto")))
        old = os.environ.copy()
        os.environ.update(GIT_ENV)
        try:
            rc = rs.main(["--gates", str(path), "--target", "guidance", "--guidance",
                          str(root), "--allow-mode", "auto"], env=AUTO_ENV, gh=FakeGh(),
                         push=lambda c, b: None, registry_gh=registry, out=io.StringIO())
        finally:
            os.environ.clear()
            os.environ.update(old)
        self.assertEqual(rc, 2)

    def test_fallback_to_proposal_files_the_issue_when_auto_is_not_allowed(self):
        root = guidance_repo(self)
        rc, gh, pushed, out = self.run_main(guidance_gates(mode="auto"), root,
                                            allow=("proposal",), fallback=True)
        self.assertEqual(rc, 0, out)
        self.assertEqual(pushed, [])
        self.assertEqual(self.registry.calls, [])
        self.assertEqual(gh.calls[-1][0][:4], ["issue", "create", "-R", rs.EVALS_REPO])
        self.assertIn(rs.GUIDANCE_NO_CREDENTIAL, gh.calls[-1][1])
        self.assertEqual(sh(root, "git", "branch", "--list").stdout.split(), ["*", "main"])
        rc, _gh, _pushed, out = self.run_main(guidance_gates(mode="auto"), root,
                                              allow=("proposal",), fallback=True, dry=True)
        self.assertEqual(json.loads(out)["mode_reason"], rs.GUIDANCE_NO_CREDENTIAL)

    def test_without_fallback_auto_not_allowed_still_exits_2(self):
        root = guidance_repo(self)
        rc, gh, pushed, _out = self.run_main(guidance_gates(mode="auto"), root,
                                             allow=("proposal",))
        self.assertEqual(rc, 2)
        self.assertEqual(pushed, [])
        self.assertEqual(gh.calls, [])

    def test_a_sections_file_section_removes_the_file(self):
        root = guidance_repo(self)
        rc, _gh, _pushed, out = self.run_main(
            guidance_gates(mode="auto", section="section-delta"), root)
        self.assertEqual(rc, 0, out)
        self.assertFalse((root / "agents-md/sections/delta.md").exists())

    def test_auto_mode_outside_actions_or_a_dispatch_is_refused_and_writes_nothing(self):
        root = guidance_repo(self)
        head = sh(root, "git", "rev-parse", "HEAD").stdout
        for env in ({**AUTO_ENV, "GITHUB_ACTIONS": ""},
                    {**AUTO_ENV, "GITHUB_EVENT_NAME": "workflow_dispatch"}):
            rc, gh, pushed, _ = self.run_main(guidance_gates(mode="auto"), root, env=env)
            with self.subTest(env=env):
                self.assertEqual(rc, 2)
                self.assertEqual(pushed, [])
        self.assertEqual(sh(root, "git", "rev-parse", "HEAD").stdout, head)

    def test_a_dry_run_plans_and_writes_nothing(self):
        root = guidance_repo(self)
        rc, gh, pushed, out = self.run_main(guidance_gates(mode="auto"), root, env={}, dry=True)
        self.assertEqual(rc, 0, out)
        plan = json.loads(out)
        self.assertEqual(plan["action"], "guidance-removal")
        self.assertIn(["copy", "agents-md/base.md", ".claude/hooks/fleet-guidance.md"],
                      plan["would_run"])
        self.assertEqual(pushed, [])
        self.assertEqual(sh(root, "git", "status", "--porcelain").stdout, "")


class GuidanceWorkflowTests(unittest.TestCase):
    """skill-coverage.yml's retirement job, read with a YAML parser."""

    def test_the_guidance_step_follows_the_skill_step_with_its_own_scoped_token(self):
        doc = yaml.safe_load((ROOT / ".github/workflows/skill-coverage.yml").read_text())
        steps = doc["jobs"]["retirement"]["steps"]
        checkout = next(s for s in steps if s.get("with", {}).get("repository")
                        == "example-adopter/_agent-guidance")
        compute = next(s for s in steps if "scripts/compute_gates.py" in s.get("run", ""))
        acts = [s for s in steps if "scripts/retire_skill.py" in s.get("run", "")]
        self.assertEqual(len(acts), 2)
        guidance_act = acts[1]
        self.assertIn("--guidance \"$GITHUB_WORKSPACE/agent-guidance\"", compute["run"])
        self.assertIn("--target guidance", guidance_act["run"])
        self.assertNotIn("--target guidance", acts[0]["run"])
        self.assertIn("modes=(--allow-mode proposal)", guidance_act["run"])
        self.assertIn('if [ -n "$REGISTRY_TOKEN" ]; then\n  modes+=(--allow-mode auto)',
                      guidance_act["run"])
        self.assertIn("modes+=(--fallback-to-proposal)", guidance_act["run"])
        self.assertIn('"${modes[@]}"', guidance_act["run"])
        self.assertEqual(guidance_act["env"], {
            "GH_TOKEN": "${{ github.token }}",
            "REGISTRY_TOKEN": "${{ steps.guidance-token.outputs.token }}"})
        mint = steps[steps.index(guidance_act) - 1]
        self.assertEqual(mint["id"], "guidance-token")
        self.assertTrue(mint["uses"].startswith("actions/create-github-app-token@"))
        self.assertIs(mint["continue-on-error"], True)
        self.assertEqual(mint["if"], "steps.credential.outputs.present == 'true'")
        self.assertEqual(mint["with"], {
            "client-id": "${{ secrets.EVAL_IMPROVE_APP_CLIENT_ID }}",
            "private-key": "${{ secrets.EVAL_IMPROVE_APP_PEM }}",
            "owner": "example-adopter", "repositories": "_agent-guidance",
            "permission-contents": "write", "permission-pull-requests": "write"})
        # Each token reaches one step: the guidance token is not the skill step's.
        self.assertEqual([s for s in steps if "guidance-token" in json.dumps(s)],
                         [mint, guidance_act])
        self.assertNotIn("guidance-token", json.dumps(acts[0]))
        self.assertLess(steps.index(checkout), steps.index(compute))
        self.assertIs(checkout["with"]["persist-credentials"], False)
        self.assertEqual(doc["jobs"]["retirement"]["permissions"],
                         {"contents": "read", "issues": "write"})


def _real_checkout_ready() -> bool:
    return all((REAL_GUIDANCE_DIR / p).exists() for p in (
        "scripts/check-guidance-touch.js", "scripts/check-agents-md.sh",
        "scripts/check-fleet-guidance-mirror.js", "node_modules/yaml")) \
        and shutil.which("node") is not None


@unittest.skipUnless(_real_checkout_ready(), f"no prepared _agent-guidance checkout at "
                     f"{REAL_GUIDANCE_DIR} (scripts and node_modules)")
class RealGuidanceRemovalTests(unittest.TestCase):
    """The removal against a local clone of the real _agent-guidance, judged
    by that repository's own checks. The clone has its remote removed and
    the push is a fake, so nothing leaves the scratch directory."""

    def test_a_removal_passes_the_repositorys_own_checks(self):
        clone = tmpdir(self, "ag-real-") / "ag"
        sh(clone.parent, "git", "clone", "-q", "--no-hardlinks", str(REAL_GUIDANCE_DIR),
           str(clone))
        sh(clone, "git", "remote", "remove", "origin")
        (clone / "node_modules").symlink_to(REAL_GUIDANCE_DIR / "node_modules")
        # A symlink does not match the checkout's `node_modules/` ignore rule.
        with open(clone / ".git" / "info" / "exclude", "a", encoding="utf-8") as f:
            f.write("/node_modules\n")
        base = sh(clone, "git", "rev-parse", "HEAD").stdout.strip()
        manifest = yaml.safe_load((clone / "agents-md/eval-coverage.yml").read_text())
        section = next(r["id"] for r in manifest if r["file"] == "agents-md/base.md")
        doc = guidance_gates(mode="auto", section=section)
        doc["as_of"] = sh(clone, "git", "log", "-1", "--format=%cd",
                          "--date=format-local:%Y%m%dT%H%M%SZ",
                          env={"TZ": "UTC"}).stdout.strip()
        rc, _gh, pushed, out = GuidanceRemovalTests.run_main(self, doc, clone)
        self.assertEqual(rc, 0, out)
        head = sh(clone, "git", "rev-parse", "HEAD").stdout.strip()
        event = clone.parent / "event.json"
        event.write_text(json.dumps({"pull_request": {"base": {"sha": base},
                                                      "head": {"sha": head}}}))
        sh(clone, "bash", "scripts/check-agents-md.sh", "AGENTS.md")
        sh(clone, "node", "scripts/check-guidance-coverage.js", "--check-bytes")
        sh(clone, "node", "scripts/check-guidance-touch.js",
           env={"GITHUB_EVENT_PATH": str(event)})
        sh(clone, "node", "scripts/check-fleet-guidance-mirror.js")
        expected = sh(clone, "bash", "-c",
                      "printf '%s\\n%s\\n' \"$(./scripts/build-agents-md.sh)\" "
                      "\"$(sed -n '/^## Repo-specific additions/,$p' AGENTS.md)\"").stdout
        self.assertEqual((clone / "AGENTS.md").read_text(), expected)


if __name__ == "__main__":
    unittest.main()
