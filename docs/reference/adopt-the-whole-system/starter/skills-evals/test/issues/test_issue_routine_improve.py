#!/usr/bin/env python3
"""#71 / ADR 0005 amendment 1 / ADR 0010: the routine's improve mode.

The eval routine runs `scripts/propose_skill_edit.py` and, on an ACCEPTED
candidate only, pushes one branch `claude/eval-improve-<run id>` holding
`eval-improve/<run id>/{summary.json, report.md, skill.patch}` (the record,
the pull-request body and the registry patch the loop writes). It never opens
a pull request. `scripts/improve_gate.py` validates that branch from the
default branch's checkout, through git plumbing only, and
`.github/workflows/routine-improve-gate.yml` opens a pull request in
example-adopter/acsi-agentskills, auto-merging or draft by
evals/improve-policy.yml, but only when the owner-chosen credential exists. What is pinned here:

  * the branch contract: exactly the three files, additions only, under the
    branch's own run id; an accepted record for acsi-agentskills whose
    SKILL.md path matches its skill; a single-file patch whose hunks parse;
    a report with the loop's own title and no closing keyword or @mention;
    every rejection names a path and a reason, never file content;
  * `apply` changes only that SKILL.md, refuses a patch that does not apply
    and any change to the frontmatter's name;
  * the improve root is disjoint from the results ingest's root, so an
    improve push never starts a results ingest, and back;
  * both workflows, parsed with PyYAML: workflow_run for the gate, no
    permissions on the signal, `contents: read` on every gate job, the
    cross-repository credential only in the registry-pr job and that job
    skipped unless the credential exists, the registry built before the
    token is minted, `--draft` unless the mode is `auto`, auto-merge only by
    merge commit and only in auto mode, after the labels, one loop pull
    request at a time, no force, no `${{ }}` in a `run:`, locked pins, no
    concurrency group;
  * `prepare`: the patch, the plugin's patch version in both manifests, a
    docs/skill-impact.md entry with the record's numbers, a PURPOSE.md stub
    only when missing, the README table and a skills.lock re-pin commit,
    nothing else; `draft` when the plugin runs code or the policy says so;
    refused when the skill moved since it was measured;
  * evals/improve-policy.yml: `auto` committed, a missing key `auto`,
    anything malformed `draft`;
  * #71's gate: no workflow that fires the routine or touches an improve
    branch has a schedule.

Fixtures are built in-test in temporary git repositories (example.com
only). Discovered and run by test/run_tests.py.
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from fnmatch import fnmatch
from pathlib import Path
from unittest import mock

import yaml

TEST_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = TEST_DIR.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
GATE = WORKFLOWS / "routine-improve-gate.yml"
SIGNAL = WORKFLOWS / "routine-improve-pushed.yml"
RESULTS_SIGNAL = WORKFLOWS / "routine-eval-results-pushed.yml"
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(TEST_DIR))
from lockfile_pins import lock_problem  # noqa: E402
import example_evals  # noqa: E402

import improve_gate as gate  # noqa: E402
import ingest_routine_results as ingest  # noqa: E402
import propose_skill_edit as pse  # noqa: E402

RUN_ID = "20261006T201500Z-0a1b2c"
BRANCH = f"claude/eval-improve-{RUN_ID}"
SKILL = "writing-adrs"
SKILL_MD = f"plugins/acsi-anything-anywhere/skills/{SKILL}/SKILL.md"
REGISTRY_SHA = "1" * 40
MARKER = "do-not-echo-this-marker"
ROUTINE_ID = "ROUTINE_TRIGGER_ID"

GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.com",
    "GIT_COMMITTER_NAME": "fixture",
    "GIT_COMMITTER_EMAIL": "fixture@example.com",
    "GIT_AUTHOR_DATE": "2026-10-06T00:00:00Z",
    "GIT_COMMITTER_DATE": "2026-10-06T00:00:00Z",
    "GIT_CONFIG_COUNT": "2",
    "GIT_CONFIG_KEY_0": "gc.auto", "GIT_CONFIG_VALUE_0": "0",
    "GIT_CONFIG_KEY_1": "maintenance.auto", "GIT_CONFIG_VALUE_1": "false",
}

ORIGINAL = ("---\nname: writing-adrs\ndescription: Write an ADR.\n---\n\n"
            "# Writing ADRs\n\nKeep it short.\n\nLink the issue.\n")
CANDIDATE = ("---\nname: writing-adrs\ndescription: Write an ADR.\n---\n\n"
             "# Writing ADRs\n\nKeep it short.\n\nName the decider.\n\n"
             "Link the issue.\n")


def metrics(passed=1) -> dict:
    return {"passed": passed, "total": 2, "judge_mean": None, "error": None,
            "trials": 1, "tokens": 1200.0}


def record(**changes) -> dict:
    """An accepted record in the shape propose_skill_edit.improve() writes."""
    doc = {
        "schema": 1, "skill": SKILL, "timestamp": "20261006T201733Z",
        "local_exhibit": "skills-evals ADR 0002 decision 4: operator login, "
                         "not badge input",
        "registry": {"name": "acsi-agentskills",
                     "url": "https://github.com/example-adopter/acsi-agentskills",
                     "sha": REGISTRY_SHA, "skill_md": SKILL_MD},
        "split": {"rotation": 0, "train": ["bootstrap", "supersede"],
                  "validation": "existing-convention"},
        "trials": 1, "no_judge": True, "min_gain": 0.1,
        "models": {"arm": "model-a", "proposal": "model-b"},
        "baseline": {"bootstrap": metrics(1)},
        "candidate": {"bootstrap": metrics(2)},
        "runs": {"baseline": "/tmp/runs/baseline/writing-adrs/x",
                 "candidate": "/tmp/runs/candidate/writing-adrs/x"},
        "files": {"patch": "/tmp/improvements/writing-adrs/x.patch",
                  "pr_body": "/tmp/improvements/writing-adrs/x.pr-body.md",
                  "record": "/tmp/improvements/writing-adrs/x.json"},
        "trigger_set": {"source": "fixtures", "size": 21,
                        "split": {"train": {"true": 2, "false": 10}},
                        "problems": []},
        "description_half": {"changed": False, "best": "Write an ADR.",
                             "original": "Write an ADR."},
        "body_half": {"model": "model-b", "rationale": "Name the decider.",
                      "unified_diff": "--- a/SKILL.md\n+++ b/SKILL.md\n",
                      "error": None},
        "status": "accepted",
        "reasons": ["train objective +0.50", "validation held"],
        "table": "| Fixture | Split |\n|---|---|",
    }
    doc.update(changes)
    return doc


def patch_text(skill_md=SKILL_MD, old=ORIGINAL, new=CANDIDATE) -> str:
    return pse.registry_patch(old, new, skill_md)


def report_text(skill=SKILL, extra="") -> str:
    return (f"## Proposed `{skill}` SKILL.md edit (validation-gated)\n\n"
            f"Decision: train improved.{extra}\n")


def good_files(run_id=RUN_ID, root="eval-improve", **overrides) -> dict:
    base = f"{root}/{run_id}"
    files = {f"{base}/summary.json": json.dumps(record(), indent=2).encode(),
             f"{base}/report.md": report_text().encode(),
             f"{base}/skill.patch": patch_text().encode()}
    for name, data in overrides.items():
        files[f"{base}/{name.replace('_', '.', 1)}"] = data
    return files


class GitCase(unittest.TestCase):
    """A scratch skills-evals with `main` (holding evals/<skill>/) and an
    improve branch."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        env = mock.patch.dict(os.environ, GIT_ENV)
        env.start()
        self.addCleanup(env.stop)
        self.git("init", "-q", "-b", "main")
        self.write({"README.md": b"see https://example.com\n",
                    ".github/workflows/ci.yml": b"name: CI\n",
                    f"evals/{SKILL}/bootstrap/fixture.yaml": b"name: x\n"})
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "main")
        self.git("checkout", "-q", "-b", BRANCH)

    def git(self, *args) -> str:
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def write(self, files: dict) -> None:
        for name, data in files.items():
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    def commit(self, files: dict | None = None) -> str:
        self.write(good_files() if files is None else files)
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", "improve")
        return self.git("rev-parse", "HEAD")

    def validate(self, branch=BRANCH, expect=None, out=None):
        out = out or self.tmp / "staged"
        return gate.validate(str(self.repo), "main", branch, branch, expect,
                             out)

    def assertRejected(self, pattern=None, **kwargs):
        with self.assertRaises(ingest.Rejected) as caught:
            self.validate(**kwargs)
        if pattern:
            self.assertRegex(str(caught.exception), pattern)
        self.assertNotIn(MARKER, str(caught.exception))
        staged = self.tmp / "staged"
        self.assertFalse(staged.exists() and any(staged.iterdir()),
                         "a rejected branch stages nothing")
        return caught.exception


class AcceptTests(GitCase):

    def test_accepts_the_contract_and_stages_exact_bytes(self):
        files = good_files()
        sha = self.commit(files)
        out = self.validate(expect=sha)
        self.assertEqual(out, {
            "run_id": RUN_ID, "sha": sha, "skill": SKILL,
            "registry_sha": REGISTRY_SHA, "skill_md": SKILL_MD,
            "target_branch": f"eval-improve/{SKILL}"})
        staged = self.tmp / "staged"
        self.assertEqual(sorted(p.name for p in staged.iterdir()),
                         ["report.md", "skill.patch", "summary.json"])
        for name, data in files.items():
            self.assertEqual((staged / Path(name).name).read_bytes(), data)

    def test_the_loops_own_pr_body_is_an_accepted_report(self):
        rec = record()
        body = pse.pr_body(rec, pse.table(
            {"bootstrap": metrics(1)}, {"bootstrap": metrics(2)},
            ["bootstrap"], "existing-convention"))
        self.commit(good_files(report_md=body.encode()))
        self.assertEqual(self.validate()["skill"], SKILL)

    def test_accepts_explicit_accepted_decision(self):
        self.commit(good_files(summary_json=json.dumps(
            record(decision="accepted"), indent=2).encode()))
        self.assertEqual(self.validate()["skill"], SKILL)

    def test_accepts_metrics_with_and_without_per_model_tokens(self):
        # The fields the loop writes since per-model token reporting, beside
        # `tokens` in every fixture's metrics, and the older shape without.
        per_model = {
            "model_tokens": {"claude-opus-4-8": {
                "input_tokens": 100.0, "output_tokens": 200.0,
                "cache_read_input_tokens": None,
                "cache_creation_input_tokens": 100.0}},
            "cross_model": {"model": "model-a", "threshold": 0.5, "n": 1,
                            "flagged_trials": 1, "other_share": 0.9,
                            "flagged": True}}
        for extra in ({}, per_model):
            with self.subTest(new_fields=bool(extra)):
                rec = record(baseline={"bootstrap": {**metrics(1), **extra}},
                             candidate={"bootstrap": {**metrics(2), **extra}})
                self.commit(good_files(summary_json=json.dumps(
                    rec, indent=2).encode()))
                out = self.tmp / f"staged-{bool(extra)}"
                self.assertEqual(self.validate(out=out)["skill"], SKILL)

    def test_every_key_the_loop_writes_is_allowed(self):
        """AST-read improve() keys and the common writer's added field."""
        tree = ast.parse((REPO_ROOT / "scripts" /
                          "propose_skill_edit.py").read_text(encoding="utf-8"))
        keys = set()
        for func in (node for node in ast.walk(tree)
                     if isinstance(node, ast.FunctionDef)
                     and node.name in {"improve", "write_record"}):
            for node in ast.walk(func):
                if isinstance(node, ast.Assign) and any(
                        isinstance(t, ast.Name) and t.id == "record"
                        for t in node.targets) and isinstance(node.value, ast.Dict):
                    keys |= {k.value for k in node.value.keys
                             if isinstance(k, ast.Constant)}
                if isinstance(node, ast.Subscript) and isinstance(
                        node.value, ast.Name) and node.value.id == "record" \
                        and isinstance(node.ctx, ast.Store) \
                        and isinstance(node.slice, ast.Constant):
                    keys.add(node.slice.value)
                if isinstance(node, ast.Call) and isinstance(
                        node.func, ast.Attribute) and node.func.attr == "update" \
                        and isinstance(node.func.value, ast.Name) \
                        and node.func.value.id == "record":
                    keys |= {kw.arg for kw in node.keywords if kw.arg}
        self.assertIn("status", keys)
        self.assertLessEqual(keys, set(gate.RECORD_ALLOWED))
        self.assertLessEqual(set(gate.RECORD_REQUIRED), keys)



class RejectPathTests(GitCase):

    def test_rejects_a_non_accepted_decision(self):
        for value in (None, "rejected", "refused", 7, []):
            with self.subTest(value=value):
                self.commit(good_files(summary_json=json.dumps(
                    record(decision=value), indent=2).encode()))
                self.assertRejected(r"decision")
                self.git("reset", "--hard", "main")
                self.git("checkout", "-q", BRANCH)

    def test_accepts_either_tokens_basis_or_none(self):
        for value in ("model_usage_total", "usage_main_loop", "absent"):
            with self.subTest(value=value):
                rec = record() if value == "absent" else record(tokens_basis=value)
                self.commit(good_files(summary_json=json.dumps(
                    rec, indent=2).encode()))
                self.assertEqual(self.validate(out=self.tmp / value)["skill"], SKILL)

    def test_rejects_an_unknown_tokens_basis(self):
        for value in (None, "tokens", 7, []):
            with self.subTest(value=value):
                self.commit(good_files(summary_json=json.dumps(
                    record(tokens_basis=value), indent=2).encode()))
                self.assertRejected(r"tokens_basis")
                self.git("reset", "--hard", "main")
                self.git("checkout", "-q", BRANCH)

    def test_rejects_a_modified_main_file(self):
        self.commit({**good_files(), "README.md": b"changed\n"})
        self.assertRejected(r"README\.md.*only additions")

    def test_rejects_an_added_workflow_or_script(self):
        self.commit({**good_files(),
                     ".github/workflows/evil.yml": b"name: x\n"})
        self.assertRejected(r"evil\.yml.*not under")

    def test_rejects_an_executable_file(self):
        self.commit()
        path = f"eval-improve/{RUN_ID}/skill.patch"
        self.git("update-index", "--chmod=+x", path)
        self.git("commit", "-q", "-m", "x")
        self.assertRejected(r"mode 100755")

    def test_rejects_a_missing_or_extra_file(self):
        files = good_files()
        del files[f"eval-improve/{RUN_ID}/report.md"]
        self.commit(files)
        self.assertRejected(r"report\.md.*missing")

    def test_rejects_an_unknown_file_in_the_root(self):
        self.commit({**good_files(),
                     f"eval-improve/{RUN_ID}/SKILL.md": b"x\n"})
        self.assertRejected(r"SKILL\.md.*not a file")

    def test_rejects_the_results_root_and_another_run_id(self):
        self.commit(good_files(root="eval-results"))
        self.assertRejected(r"not under 'eval-improve/")

    def test_rejects_a_run_id_that_disagrees_with_the_branch(self):
        self.commit(good_files(run_id="20261006T201500Z-ffffff"))
        self.assertRejected(r"not under")

    def test_rejects_bad_branch_names(self):
        self.commit()
        for branch in (f"claude/eval-{RUN_ID}", f"claude/eval-improve-{RUN_ID}x",
                       "claude/eval-improve-", "main",
                       f"claude/scaffold-{RUN_ID}"):
            with self.subTest(branch=branch):
                with self.assertRaises(ingest.Rejected):
                    gate.validate(str(self.repo), "main", BRANCH, branch,
                                  None, self.tmp / "s")

    def test_rejects_a_moved_branch(self):
        self.commit()
        self.assertRejected(r"moved", expect="0" * 40)

    def test_rejects_an_over_cap_file(self):
        self.commit(good_files(
            report_md=report_text(extra="x" * gate.SIZE_CAPS["report.md"])
            .encode()))
        self.assertRejected(r"over the report\.md cap")


class RejectContentTests(GitCase):

    def rejects(self, pattern, **overrides):
        shutil.rmtree(self.tmp / "staged", ignore_errors=True)
        self.commit(good_files(**overrides))
        return self.assertRejected(pattern)

    def rec(self, **changes) -> bytes:
        return json.dumps(record(**changes)).encode()

    def test_rejects_a_record_that_was_not_accepted(self):
        for status in ("rejected", "no-candidate", "refused"):
            with self.subTest(status=status):
                self.git("reset", "-q", "--hard", "main")
                self.rejects(r"status", summary_json=self.rec(status=status))

    def test_rejects_another_registry_or_a_bad_sha(self):
        cases = [
            {"name": "example-site-platform"},
            {"url": "https://github.com/example/acsi-agentskills"},
            {"sha": "abc"},
            {"skill_md": f"plugins/x/skills/other-skill/SKILL.md"},
            {"skill_md": f"plugins/../skills/{SKILL}/SKILL.md"},
            {"skill_md": f"plugins/x/skills/{SKILL}/README.md"},
        ]
        for change in cases:
            with self.subTest(change=change):
                self.git("reset", "-q", "--hard", "main")
                reg = {**record()["registry"], **change}
                self.rejects(r"registry", summary_json=self.rec(registry=reg))

    def test_rejects_a_skill_with_no_evals_on_the_default_branch(self):
        rec = record(skill="other-skill", registry={
            **record()["registry"],
            "skill_md": "plugins/x/skills/other-skill/SKILL.md"})
        self.rejects(r"no evals/", summary_json=json.dumps(rec).encode(),
                     report_md=report_text("other-skill").encode(),
                     skill_patch=patch_text(
                         "plugins/x/skills/other-skill/SKILL.md").encode())

    def test_rejects_record_schema_violations(self):
        cases = [
            ({"schema": 2}, r"schema"),
            ({"schema": True}, r"schema"),
            ({"surprise": 1}, r"unexpected keys"),
            ({"skill": "../x"}, r"skill"),
            ({"trials": 0}, r"trials"),
            ({"reasons": "accepted"}, r"reasons"),
            ({"table": "x" * (gate.MAX_RECORD_STRING + 1)}, r"too long"),
        ]
        for change, pattern in cases:
            with self.subTest(change=list(change)):
                self.git("reset", "-q", "--hard", "main")
                self.rejects(pattern, summary_json=self.rec(**change))
        self.git("reset", "-q", "--hard", "main")
        doc = record()
        del doc["registry"]
        self.rejects(r"missing keys", summary_json=json.dumps(doc).encode())

    def test_rejects_duplicate_keys_non_utf8_and_controls(self):
        cases = [
            (b'{"schema": 1, "schema": 1}', r"duplicate"),
            (b"\xff\xfe", r"not UTF-8"),
            (("{" + '"x": "\x1b[31m' + MARKER + '"}').encode(), r"control"),
        ]
        for data, pattern in cases:
            with self.subTest(pattern=pattern):
                self.git("reset", "-q", "--hard", "main")
                self.rejects(pattern, summary_json=data)

    def test_rejects_a_patch_aimed_elsewhere(self):
        cases = [
            patch_text(skill_md="plugins/x/skills/writing-adrs/OTHER.md"),
            patch_text(skill_md=".github/workflows/ci.yml"),
            patch_text() + patch_text(skill_md="scripts/evil.py"),
            "",
            "--- a/" + SKILL_MD + "\n+++ b/" + SKILL_MD + "\n",
            re.sub(r"@@ -(\d+),(\d+)",
                   lambda m: f"@@ -{m[1]},{int(m[2]) + 1}", patch_text()),
            patch_text().replace("\n+Name", "\n?Name"),
            patch_text() + "trailing garbage " + MARKER + "\n",
        ]
        for text in cases:
            with self.subTest(text=text[:60]):
                self.git("reset", "-q", "--hard", "main")
                self.rejects(r"skill\.patch", skill_patch=text.encode())

    def test_patch_paths_must_match_the_record(self):
        other = f"plugins/other-plugin/skills/{SKILL}/SKILL.md"
        self.rejects(r"skill\.patch",
                     skill_patch=patch_text(skill_md=other).encode())

    def test_rejects_a_report_that_could_act_on_github(self):
        cases = [
            (report_text(skill="other-skill"), r"title"),
            (report_text(extra="\n\nCloses #12"), r"closing keyword"),
            (report_text(extra=" fixes Adam-S-Daniel/x#3"), r"closing keyword"),
            (report_text(extra=" Resolved: https://github.com/a/b/issues/4"),
             r"closing keyword"),
            (report_text(extra=" cc @someone " + MARKER), r"mention"),
            # Markdown around a keyword or a reference does not hide it.
            (report_text(extra="\n\n**Fixes** #1"), r"closing keyword"),
            (report_text(extra="\n\n_Closes_ #2"), r"closing keyword"),
            (report_text(extra="\n\n`Resolves` #5"), r"closing keyword"),
            (report_text(extra="\n\nFixes [#1](https://github.com/o/r/issues/1)"),
             r"closing keyword"),
            (report_text(extra="\n\nCloses [the bug](https://github.com/o/r/issues/7)"),
             r"closing keyword"),
            (report_text(extra="\n\nfixes: <https://github.com/o/r/pull/8>"),
             r"closing keyword"),
            # Punctuation before an @ does not hide a mention.
            (report_text(extra=" _@octocat_"), r"mention"),
            (report_text(extra=" see.@octocat"), r"mention"),
            (report_text(extra=" x-@octocat"), r"mention"),
            (report_text(extra=" a /@octocat"), r"mention"),
            (report_text(extra=" (`@octocat`)"), r"mention"),
        ]
        for text, pattern in cases:
            with self.subTest(pattern=pattern, text=text[-30:]):
                self.git("reset", "-q", "--hard", "main")
                self.rejects(pattern, report_md=text.encode())

    def test_an_email_or_pin_is_not_a_mention(self):
        self.commit(good_files(report_md=report_text(
            extra=" bot@example.com uses actions/checkout@" + "a" * 40)
            .encode()))
        self.assertEqual(self.validate()["run_id"], RUN_ID)

    def test_cli_rejection_never_quotes_content(self):
        self.commit(good_files(report_md=report_text(
            extra=" @x " + MARKER).encode()))
        err = io.StringIO()
        with contextlib.redirect_stderr(err), \
                contextlib.redirect_stdout(io.StringIO()):
            rc = gate.main(["validate", "--repo", str(self.repo),
                            "--base", "main", "--source", BRANCH,
                            "--branch", BRANCH,
                            "--out", str(self.tmp / "staged")])
        self.assertEqual(rc, 1)
        self.assertIn("rejected:", err.getvalue())
        self.assertNotIn(MARKER, err.getvalue())


class ResolveTests(unittest.TestCase):

    def event(self, doc) -> str:
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        self.addCleanup(os.unlink, handle.name)
        with handle:
            json.dump(doc, handle)
        return handle.name

    def push_run(self, branch=BRANCH, head_repo="o/skills-evals") -> dict:
        return {"repository": {"full_name": "o/skills-evals"},
                "workflow_run": {"event": "push", "head_branch": branch,
                                 "head_sha": "a" * 40,
                                 "head_repository": {"full_name": head_repo}}}

    def test_workflow_run_and_dispatch(self):
        self.assertEqual(
            gate.resolve("workflow_run", self.event(self.push_run())),
            {"branch": BRANCH, "sha": "a" * 40, "run_id": RUN_ID})
        self.assertEqual(
            gate.resolve("workflow_dispatch",
                         self.event({"inputs": {"branch": BRANCH}})),
            {"branch": BRANCH, "sha": "", "run_id": RUN_ID})

    def test_rejections(self):
        cases = [
            ("workflow_run", self.push_run(branch=f"claude/eval-{RUN_ID}")),
            ("workflow_run", self.push_run(head_repo="fork/skills-evals")),
            ("workflow_dispatch", {"inputs": {"branch": "main"}}),
            ("push", {"ref": "refs/heads/" + BRANCH}),
        ]
        for name, doc in cases:
            with self.subTest(name=name, doc=doc):
                with self.assertRaises(ingest.Rejected):
                    gate.resolve(name, self.event(doc))


class ApplyTests(unittest.TestCase):
    """`apply` against a scratch registry checkout."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        env = mock.patch.dict(os.environ, GIT_ENV)
        env.start()
        self.addCleanup(env.stop)
        self.tree = self.tmp / "registry"
        self.tree.mkdir()
        self.run_git("init", "-q", "-b", "main")
        (self.tree / SKILL_MD).parent.mkdir(parents=True)
        (self.tree / SKILL_MD).write_text(ORIGINAL, encoding="utf-8")
        (self.tree / "README.md").write_text("x\n", encoding="utf-8")
        self.run_git("add", "-A")
        self.run_git("commit", "-q", "-m", "registry")
        self.staged = self.tmp / "staged"
        self.staged.mkdir()

    def run_git(self, *args) -> str:
        return subprocess.run(["git", "-C", str(self.tree), *args], check=True,
                              capture_output=True, text=True).stdout

    def stage(self, patch: str) -> None:
        (self.staged / "skill.patch").write_text(patch, encoding="utf-8")

    def test_applies_and_changes_only_the_skill(self):
        self.stage(patch_text())
        self.assertEqual(gate.apply(self.staged, self.tree, SKILL_MD),
                         {"changed": SKILL_MD})
        self.assertEqual((self.tree / SKILL_MD).read_text(encoding="utf-8"),
                         CANDIDATE)
        self.assertEqual(self.run_git("status", "--porcelain").split(),
                         ["M", SKILL_MD])

    def test_a_description_change_is_allowed(self):
        new = CANDIDATE.replace("Write an ADR.", "Write or supersede an ADR.")
        self.stage(patch_text(new=new))
        gate.apply(self.staged, self.tree, SKILL_MD)

    def test_refuses_a_name_or_frontmatter_key_change(self):
        for new in (CANDIDATE.replace("name: writing-adrs", "name: other"),
                    CANDIDATE.replace("description:", "allowed-tools: Bash\n"
                                      "description:")):
            with self.subTest(new=new[:50]):
                self.run_git("checkout", "-q", "--", ".")
                self.stage(patch_text(new=new))
                with self.assertRaisesRegex(ingest.Rejected, "frontmatter"):
                    gate.apply(self.staged, self.tree, SKILL_MD)

    def test_refuses_any_other_frontmatter_value_change(self):
        """propose_skill_edit.set_description's rule: only `description`
        may change in the frontmatter."""
        original = ORIGINAL.replace("description:", "allowed-tools: Read\n"
                                    "description:")
        (self.tree / SKILL_MD).write_text(original, encoding="utf-8")
        self.run_git("commit", "-q", "-am", "tools")
        for new in (original.replace("allowed-tools: Read",
                                     "allowed-tools: Bash(*)"),
                    original.replace("allowed-tools: Read",
                                     "allowed-tools: [Read, Bash]")):
            with self.subTest(new=new[:70]):
                self.run_git("checkout", "-q", "--", ".")
                self.stage(patch_text(old=original, new=new))
                with self.assertRaisesRegex(ingest.Rejected, "frontmatter"):
                    gate.apply(self.staged, self.tree, SKILL_MD)

    def test_refuses_a_patch_that_changes_a_second_file(self):
        """`validate` already refuses such a patch; `apply` checks the tree
        on its own, so a patch staged any other way cannot widen it."""
        self.stage(patch_text() + pse.registry_patch("x\n", "y\n", "README.md"))
        with self.assertRaisesRegex(ingest.Rejected, "more than the one"):
            gate.apply(self.staged, self.tree, SKILL_MD)

    def test_refuses_a_patch_that_does_not_apply(self):
        self.stage(patch_text(old=ORIGINAL.replace("Keep", "Make"),
                              new=CANDIDATE.replace("Keep", "Make")))
        with self.assertRaisesRegex(ingest.Rejected, "does not apply"):
            gate.apply(self.staged, self.tree, SKILL_MD)
        self.assertEqual(self.run_git("status", "--porcelain"), "")

    def test_refuses_a_symlinked_skill(self):
        target = self.tmp / "outside.md"
        target.write_text(ORIGINAL, encoding="utf-8")
        (self.tree / SKILL_MD).unlink()
        (self.tree / SKILL_MD).symlink_to(target)
        self.stage(patch_text())
        with self.assertRaisesRegex(ingest.Rejected, "symlink"):
            gate.apply(self.staged, self.tree, SKILL_MD)
        self.assertEqual(target.read_text(encoding="utf-8"), ORIGINAL)


class PrBodyTests(unittest.TestCase):

    def body(self, mode):
        staged = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, staged)
        (staged / "report.md").write_text(report_text(), encoding="utf-8")
        return gate.pr_body(staged, RUN_ID, "a" * 40, BRANCH, mode)

    def test_body_wraps_the_report_and_closes_nothing(self):
        for mode in gate.MERGE_MODES:
            with self.subTest(mode=mode):
                body = self.body(mode)
                self.assertIn(report_text(), body)
                self.assertIn(BRANCH, body)
                self.assertIn("Part of the improvement loop (https://github.com/"
                              "example-adopter/skills-evals/blob/main/docs/"
                              "decisions/0005-improvement-loop-reuses-skill-"
                              "creator.md)", body)
                self.assertNotIn("/issues/", body)
                self.assertIsNone(gate.CLOSING_RE.search(body))
                self.assertIsNone(gate.CLOSING_URL_RE.search(
                    gate.closing_text(body)))
                self.assertTrue(body.endswith("\n" + gate.AGENT_MARKER + "\n"),
                                "the fleet's agent marker ends the body")

    def test_the_mode_decides_what_the_body_promises(self):
        auto, draft = self.body("auto"), self.body("draft")
        self.assertIn("merges itself (merge commit)", auto)
        self.assertIn(gate.REGISTRY_ADR, auto)
        self.assertNotIn("nothing merges itself", auto.split("---")[0])
        self.assertIn("nothing merges itself", draft)
        self.assertNotIn(gate.REGISTRY_ADR, draft)
        self.assertEqual(gate.AGENT_MARKER, "<sub>🤖 Written by Claude Code on "
                         "behalf of @example-adopter</sub>")


class MergeModeTests(unittest.TestCase):
    """evals/improve-policy.yml's `improve_merge_mode` (the owner, 2026-10-26:
    accepted improvements merge with no human in the loop)."""

    def mode(self, text):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        path = tmp / "improve-policy.yml"
        if text is not None:
            path.write_text(text, encoding="utf-8")
        return gate.merge_mode(path)

    def test_the_committed_policy_is_auto(self):
        self.assertEqual(gate.merge_mode(), "auto")
        self.assertEqual(gate.POLICY, REPO_ROOT / "evals" / "improve-policy.yml")

    def test_a_missing_key_is_auto(self):
        self.assertEqual(self.mode(""), "auto")
        self.assertEqual(self.mode("# nothing set\n"), "auto")
        self.assertEqual(self.mode("improve_merge_mode: draft\n"), "draft")

    def test_anything_malformed_is_draft(self):
        for text in (None, "improve_merge_mode: yes\n",
                     "improve_merge_mode: Auto\n", "improve_merge_mode: [auto]\n",
                     "improve_merge_mode: auto\nretirement_mode: auto\n",
                     "- auto\n", "improve_merge_mode: 'auto\n",
                     "improve_merge_mode: 2026-13-05\n"):
            with self.subTest(text=text):
                self.assertEqual(self.mode(text), "draft")


PLUGIN = "acsi-anything-anywhere"
FAKE_README = """import pathlib
root = pathlib.Path(__file__).resolve().parent.parent
md = root / "plugins/{plugin}/skills/{skill}/SKILL.md"
description = md.read_text(encoding="utf-8").split("description: ", 1)[1].split(chr(10), 1)[0]
(root / "README.md").write_text("table: " + description + chr(10), encoding="utf-8")
""".format(plugin=PLUGIN, skill=SKILL)
#: Stands in for acsi-agentskills' generate_skills_lock.py: `--check-current`
#: is current when the lock pins HEAD, `--repin` pins HEAD; LOCK_EXIT forces
#: an exit code, STRAY makes it write a file nobody asked for.
FAKE_LOCK = """import json, os, pathlib, subprocess, sys
root = pathlib.Path(__file__).resolve().parent.parent
if os.environ.get("LOCK_EXIT"):
    sys.exit(int(os.environ["LOCK_EXIT"]))
if os.environ.get("STRAY"):
    (root / "stray.txt").write_text("x", encoding="utf-8")
lock = root / "skills.lock"
doc = json.loads(lock.read_text(encoding="utf-8"))
head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                      capture_output=True, text=True, check=True).stdout.strip()
if "--check-current" in sys.argv:
    sys.exit(0 if doc["ref"] == head else 1)
if "--repin" in sys.argv and sys.argv[sys.argv.index("--repo") + 1] == ".":
    doc["ref"] = head
    lock.write_text(json.dumps(doc) + chr(10), encoding="utf-8")
    sys.exit(0)
sys.exit(3)
"""
IMPACT = ("# skill-impact.md\n\nEntry format here.\n\n---\n\n"
          "## 2026-10-23 — other/skill — edit\n\n- Motivation: older.\n")


class PrepareTests(unittest.TestCase):
    """`prepare` builds what acsi-agentskills' required checks ask of a
    skill edit, against a scratch registry whose README and lock scripts
    are stand-ins."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        env = mock.patch.dict(os.environ, GIT_ENV)
        env.start()
        self.addCleanup(env.stop)
        for name in ("LOCK_EXIT", "STRAY"):
            os.environ.pop(name, None)
        self.tree = self.tmp / "registry"
        self.tree.mkdir()
        self.git("init", "-q", "-b", "main")
        self.write({
            SKILL_MD: ORIGINAL,
            f"plugins/{PLUGIN}/skills/{SKILL}/references/x.md": "ref\n",
            f"plugins/{PLUGIN}/plugin.json":
                '{\n  "name": "%s",\n  "version": "1.0.0"\n}\n' % PLUGIN,
            f"plugins/{PLUGIN}/.claude-plugin/plugin.json":
                '{\n  "name": "%s",\n  "version": "1.0.0",\n'
                '  "author": { "name": "Fixture" }\n}\n' % PLUGIN,
            "docs/skill-impact.md": IMPACT,
            "README.md": "table: Write an ADR.\n",
            "scripts/generate_readme_table.py": FAKE_README,
            "scripts/generate_skills_lock.py": FAKE_LOCK,
            "skills.lock": '{"ref": "none"}\n',
        })
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "registry")
        self.measured = self.git("rev-parse", "HEAD")
        self.staged = self.tmp / "staged"
        self.staged.mkdir()
        self.stage()

    def git(self, *args) -> str:
        return subprocess.run(["git", "-C", str(self.tree), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def write(self, files: dict) -> None:
        for name, text in files.items():
            path = self.tree / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def stage(self, new=CANDIDATE, doc=None) -> None:
        doc = doc or record(split={"rotation": 0, "train": ["bootstrap"],
                                   "validation": "existing-convention",
                                   "holdout": "supersede"},
                            baseline={"bootstrap": metrics(1),
                                      "existing-convention": metrics(2),
                                      "supersede": {"passed": True}},
                            candidate={"bootstrap": metrics(2),
                                       "existing-convention": metrics(2)})
        (self.staged / "summary.json").write_text(json.dumps(doc),
                                                  encoding="utf-8")
        (self.staged / "skill.patch").write_text(patch_text(new=new),
                                                 encoding="utf-8")
        (self.staged / "report.md").write_text(report_text(), encoding="utf-8")

    def prepare(self, policy_text="improve_merge_mode: auto\n"):
        policy = self.tmp / "improve-policy.yml"
        policy.write_text(policy_text, encoding="utf-8")
        return gate.prepare(self.staged, self.tree, SKILL_MD, self.measured,
                            RUN_ID, policy)

    def changed(self) -> list[str]:
        return sorted(self.git("diff", "--name-only", self.measured,
                               "HEAD").split("\n"))

    def show(self, rel: str) -> str:
        return (self.tree / rel).read_text(encoding="utf-8")

    def test_auto_builds_the_content_commit_and_the_lock_commit(self):
        new = CANDIDATE.replace("Write an ADR.", "Write or supersede an ADR.")
        self.stage(new=new)
        # The fixture identity would override the gate's own; the workflow
        # sets none.
        for name in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
                     "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
            os.environ.pop(name)
        out = self.prepare()
        self.assertEqual(out["mode"], "auto")
        self.assertNotIn("mode_reason", out)
        self.assertEqual(out["base_sha"], self.measured)
        self.assertEqual(out["version"], "1.0.1")
        self.assertEqual(out["commit"], self.git("rev-parse", "HEAD"))
        self.assertEqual(self.git("rev-parse", "HEAD~1"), out["content_sha"])
        self.assertEqual(self.git("rev-parse", "HEAD~2"), self.measured)
        self.assertEqual(self.git("show", "--name-only", "--format=", "HEAD"),
                         "skills.lock")
        self.assertEqual(json.loads(self.show("skills.lock"))["ref"],
                         out["content_sha"])
        skill_dir = f"plugins/{PLUGIN}/skills/{SKILL}"
        self.assertEqual(self.changed(), sorted([
            "README.md", "docs/skill-impact.md", f"plugins/{PLUGIN}/plugin.json",
            f"plugins/{PLUGIN}/.claude-plugin/plugin.json", "skills.lock",
            f"{skill_dir}/SKILL.md", f"{skill_dir}/PURPOSE.md"]))
        self.assertEqual(self.show(SKILL_MD), new)
        self.assertEqual(self.show("README.md"),
                         "table: Write or supersede an ADR.\n")
        for name in gate.MANIFESTS:
            text = self.show(f"plugins/{PLUGIN}/{name}")
            self.assertIn('"version": "1.0.1"', text)
            self.assertEqual(text.replace("1.0.1", "1.0.0"),
                             self.git("show", f"{self.measured}:plugins/{PLUGIN}/{name}")
                             + "\n", "only the version moved")
        self.assertIn(f"# Purpose — {SKILL}", self.show(f"{skill_dir}/PURPOSE.md"))
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertIn("Agent: claude-code", self.git("log", "-1", "--format=%B",
                                                     "HEAD~1"))
        self.assertEqual(self.git("log", "-1", "--format=%an <%ae>"),
                         "skills-evals improve-gate bot "
                         "<skills-evals@users.noreply.github.com>")

    def test_the_impact_entry_is_newest_first_and_carries_the_numbers(self):
        self.prepare()
        text = self.show("docs/skill-impact.md")
        head, rest = text.split("\n---\n\n", 1)
        self.assertEqual(head + "\n---", IMPACT.split("\n\n## ")[0])
        self.assertTrue(rest.startswith(
            f"## 2026-10-06 — {PLUGIN}/{SKILL} — edit\n\n- Motivation: "))
        self.assertIn("\n## 2026-10-23 — other/skill — edit", rest)
        entry = rest.split("\n## 2026-10-23", 1)[0]
        for line in ("- Motivation:", "- Change:", "- Eval:", "- Outcome:"):
            self.assertEqual(entry.count("\n" + line), 1, line)
        self.assertIn("train bootstrap 1/2 -> 2/2; validation "
                      "existing-convention 2/2 -> 2/2; holdout supersede "
                      "n/a -> n/a", entry)
        self.assertIn("1.0.0 -> 1.0.1", entry)
        self.assertIn("auto-merges once required checks pass", entry)
        self.assertIn("a `PURPOSE.md` stub added", entry)
        self.assertIsNone(gate.MENTION_RE.search(entry))
        self.assertIsNone(gate.CLOSING_RE.search(gate.closing_text(entry)))

    def test_an_existing_purpose_is_kept(self):
        purpose = f"plugins/{PLUGIN}/skills/{SKILL}/PURPOSE.md"
        self.write({purpose: "# Purpose\n\nHand-written.\n"})
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "purpose")
        self.measured = self.git("rev-parse", "HEAD")
        self.prepare()
        self.assertEqual(self.show(purpose), "# Purpose\n\nHand-written.\n")
        self.assertNotIn(purpose, self.changed())
        self.assertNotIn("PURPOSE.md", self.show("docs/skill-impact.md"))

    def test_a_current_lock_gets_no_second_commit(self):
        os.environ["LOCK_EXIT"] = "0"
        out = self.prepare()
        self.assertEqual(out["commit"], out["content_sha"])
        self.assertNotIn("skills.lock", self.changed())

    def test_a_registry_without_a_lock_gets_no_second_commit(self):
        self.git("rm", "-q", "skills.lock")
        self.git("commit", "-q", "-m", "no lock")
        self.measured = self.git("rev-parse", "HEAD")
        out = self.prepare()
        self.assertEqual(out["commit"], out["content_sha"])

    def test_the_policy_can_ask_for_a_draft(self):
        out = self.prepare("improve_merge_mode: draft\n")
        self.assertEqual(out["mode"], "draft")
        self.assertNotIn("mode_reason", out)
        self.assertIn("draft pull request; a person merges or closes it",
                      self.show("docs/skill-impact.md"))

    def test_a_plugin_that_runs_code_falls_back_to_a_draft(self):
        cases = {
            "hooks": {f"plugins/{PLUGIN}/hooks/hooks.json": "{}\n"},
            "bin": {f"plugins/{PLUGIN}/bin/tool": "#!/bin/sh\n"},
            "settings": {f"plugins/{PLUGIN}/settings.json": "{}\n"},
            "manifest": {f"plugins/{PLUGIN}/.claude-plugin/plugin.json":
                         '{\n  "name": "%s",\n  "version": "1.0.0",\n'
                         '  "hooks": "./h.json"\n}\n' % PLUGIN},
        }
        start = self.measured
        for label, files in cases.items():
            with self.subTest(case=label):
                self.git("checkout", "-q", "-f", "--detach", start)
                self.git("clean", "-qfdx")
                self.write(files)
                self.git("add", "-A")
                self.git("commit", "-q", "-m", label)
                self.measured = self.git("rev-parse", "HEAD")
                out = self.prepare()
                self.assertEqual(out["mode"], "draft")
                self.assertIn("runs code", out["mode_reason"])
                self.assertIn("draft pull request",
                              self.show("docs/skill-impact.md"))

    def test_refuses_a_skill_that_moved_since_it_was_measured(self):
        self.write({f"plugins/{PLUGIN}/skills/{SKILL}/references/x.md": "new\n"})
        self.git("commit", "-q", "-am", "moved")
        tip = self.git("rev-parse", "HEAD")
        with self.assertRaisesRegex(ingest.Rejected, "measure it again"):
            self.prepare()
        self.assertEqual(self.git("rev-parse", "HEAD"), tip)
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_refuses_a_dirty_checkout(self):
        self.write({"notes.txt": "x\n"})
        with self.assertRaisesRegex(ingest.Rejected, "not clean"):
            self.prepare()

    def test_refuses_manifests_that_disagree_or_are_not_semver(self):
        for label, version in (("disagree", "1.0.2"), ("not-semver", "1.0")):
            with self.subTest(case=label):
                self.git("checkout", "-q", "-f", "main")
                self.git("clean", "-qfdx")
                rel = f"plugins/{PLUGIN}/plugin.json"
                self.write({rel: '{\n  "version": "%s"\n}\n' % version})
                if label == "not-semver":
                    other = f"plugins/{PLUGIN}/.claude-plugin/plugin.json"
                    self.write({other: '{\n  "version": "%s"\n}\n' % version})
                self.git("commit", "-q", "-am", label)
                self.measured = self.git("rev-parse", "HEAD")
                with self.assertRaisesRegex(ingest.Rejected, "version"):
                    self.prepare()

    def test_refuses_when_a_registry_script_fails_or_strays(self):
        for env, pattern in ((("LOCK_EXIT", "2"), "could not run"),
                             (("STRAY", "1"), "not asked to")):
            with self.subTest(env=env):
                self.git("checkout", "-q", "-f", "--detach", self.measured)
                self.git("clean", "-qfdx")
                os.environ.pop("LOCK_EXIT", None)
                os.environ.pop("STRAY", None)
                os.environ[env[0]] = env[1]
                with self.assertRaisesRegex(ingest.Rejected, pattern):
                    self.prepare()

    def test_refuses_unvalidated_arguments(self):
        for args in (("plugins/p/skills/s/../SKILL.md", self.measured, RUN_ID),
                     (SKILL_MD, "HEAD", RUN_ID), (SKILL_MD, self.measured, "x")):
            with self.subTest(args=args):
                with self.assertRaises(ingest.Rejected):
                    gate.prepare(self.staged, self.tree, *args)

    def test_merge_mode_is_the_same_decision_for_other_gates(self):
        """`merge-mode` (routine-draft-skill-gate.yml) and `prepare` read one
        switch through one function."""
        policy = self.tmp / "p.yml"
        for text, files, want in (
                ("improve_merge_mode: auto\n", {}, ["mode=auto"]),
                ("improve_merge_mode: draft\n", {}, ["mode=draft"]),
                ("improve_merge_mode: auto\n",
                 {f"plugins/{PLUGIN}/bin/tool": "#!/bin/sh\n"},
                 ["mode=draft", "mode_reason=the skill's plugin runs code "
                  "(it has a runtime path)"])):
            with self.subTest(text=text, files=sorted(files)):
                policy.write_text(text, encoding="utf-8")
                self.write(files)
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = gate.main(["merge-mode", "--tree", str(self.tree),
                                      "--plugin", PLUGIN, "--policy", str(policy)])
                self.assertEqual((code, out.getvalue().splitlines()), (0, want))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(gate.main(["merge-mode", "--tree", str(self.tree),
                                        "--plugin", "../x"]), 1)

    def test_the_cli_prints_validated_values(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = gate.main(["prepare", "--staged", str(self.staged),
                              "--tree", str(self.tree), "--skill-md", SKILL_MD,
                              "--registry-sha", self.measured,
                              "--run-id", RUN_ID])
        self.assertEqual(code, 0)
        keys = [line.split("=", 1)[0] for line in out.getvalue().splitlines()]
        self.assertEqual(keys, ["mode", "base_sha", "content_sha", "commit",
                                "version"])


# ---------------------------------------------------------------------------
# Workflow shape, from the parsed YAML.

PUSH_TARGET = re.compile(r"\bpush\s+(?:[^\n]*?\s)?origin\s+([^\s;]+)")


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def triggers(doc: dict):
    return doc.get("on", doc.get(True))


def run_blocks(doc: dict) -> list[tuple[str, str]]:
    return [(name, step["run"]) for name, job in doc["jobs"].items()
            for step in job.get("steps") or [] if "run" in step]


def strings(node, path=()):
    """Every string scalar in a parsed document, with its key path."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from strings(value, path + (str(key),))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from strings(value, path + (str(i),))
    elif isinstance(node, str):
        yield path, node


def uses_nodes(node):
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
        cls.gate = load(GATE)
        cls.signal = load(SIGNAL)
        cls.jobs = cls.gate["jobs"]

    def test_the_gate_runs_on_workflow_run_of_the_signal(self):
        on = triggers(self.gate)
        self.assertEqual(set(on), {"workflow_run", "workflow_dispatch"})
        self.assertEqual(on["workflow_run"]["workflows"], [self.signal["name"]])
        self.assertEqual(on["workflow_run"]["types"], ["completed"])
        self.assertEqual(on["workflow_run"]["branches"],
                         ["claude/eval-improve-*"])
        self.assertEqual(set(on["workflow_dispatch"]["inputs"]), {"branch"})

    def test_signal_is_a_filtered_push_with_no_reach(self):
        on = triggers(self.signal)
        self.assertEqual(set(on), {"push"})
        self.assertEqual(on["push"]["branches"], ["claude/eval-improve-*"])
        self.assertEqual(on["push"]["paths"], [f"{gate.SOURCE_ROOT}/**"])
        self.assertEqual(self.signal["permissions"], {})
        for job in self.signal["jobs"].values():
            self.assertNotIn("permissions", job)
            for step in job["steps"]:
                self.assertNotIn("uses", step, "the signal checks nothing out")

    def test_improve_and_results_roots_are_disjoint(self):
        """`claude/eval-improve-*` also matches the results signal's
        `claude/eval-*`; only the path filters keep each push to its own
        gate."""
        results = triggers(load(RESULTS_SIGNAL))["push"]
        improve = triggers(self.signal)["push"]
        self.assertTrue(fnmatch(BRANCH, results["branches"][0]))
        improve_file = f"{gate.SOURCE_ROOT}/{RUN_ID}/summary.json"
        results_file = f"{ingest.SOURCE_ROOT}/{RUN_ID}/x/report.md"
        self.assertFalse(any(fnmatch(improve_file, p) for p in results["paths"]))
        self.assertFalse(any(fnmatch(results_file, p) for p in improve["paths"]))
        self.assertNotEqual(gate.SOURCE_ROOT, ingest.SOURCE_ROOT)
        self.assertIsNone(ingest.BRANCH_RE.fullmatch(BRANCH))

    def test_permissions_are_minimal(self):
        self.assertEqual(self.gate["permissions"], {})
        self.assertEqual(set(self.jobs), {"validate", "credential", "registry-pr"})
        self.assertEqual(self.jobs["validate"]["permissions"],
                         {"contents": "read"})
        self.assertEqual(self.jobs["credential"]["permissions"], {})
        self.assertEqual(self.jobs["registry-pr"]["permissions"],
                         {"contents": "read"})
        for doc in (self.gate, self.signal):
            self.assertNotIn("env", doc)
            for job in doc["jobs"].values():
                self.assertNotIn("env", job)

    def test_only_the_default_branch_copy_runs(self):
        self.assertIn("github.ref == format('refs/heads/{0}', "
                      "github.event.repository.default_branch)",
                      self.jobs["validate"]["if"])
        self.assertEqual(set(self.jobs["credential"]["needs"]
                             if isinstance(self.jobs["credential"]["needs"], list)
                             else [self.jobs["credential"]["needs"]]),
                         {"validate"})

    def mint_step(self) -> dict:
        steps = [s for s in self.jobs["registry-pr"]["steps"]
                 if str(s.get("uses", "")).startswith(
                     "actions/create-github-app-token@")]
        self.assertEqual(len(steps), 1, "one step mints the App token")
        return steps[0]

    def test_the_app_secrets_reach_only_the_mint_step(self):
        """Owner decision (the owner): "GitHub App (Recommended)". The App's two
        secrets reach the credential check as one boolean and the mint step
        as its inputs; nothing else."""
        client, pem = gate.APP_CLIENT_ID_SECRET, gate.APP_PEM_SECRET
        refs = sorted((path, text.strip()) for path, text in strings(self.gate)
                      if "secrets." in text)
        mint = self.jobs["registry-pr"]["steps"].index(self.mint_step())
        self.assertEqual(refs, sorted([
            (("jobs", "credential", "steps", "0", "env", "PR_CREDENTIAL_SET"),
             "${{ secrets.%s != '' && secrets.%s != '' }}" % (client, pem)),
            (("jobs", "registry-pr", "steps", str(mint), "with", "client-id"),
             "${{ secrets.%s }}" % client),
            (("jobs", "registry-pr", "steps", str(mint), "with", "private-key"),
             "${{ secrets.%s }}" % pem),
        ]))
        self.assertNotIn("secrets.", json.dumps(self.jobs["validate"]))

    def test_the_app_token_is_narrow_and_used_by_one_step(self):
        step = self.mint_step()
        self.assertEqual(step["id"], "app-token")
        self.assertEqual(step["with"]["owner"], "example-adopter")
        self.assertEqual(step["with"]["repositories"], "acsi-agentskills")
        self.assertEqual(
            {k: v for k, v in step["with"].items() if k.startswith("permission-")},
            {"permission-contents": "write", "permission-pull-requests": "write"})
        self.assertNotIn("skip-token-revoke", step["with"])
        users = [(path, text) for path, text in strings(self.gate)
                 if "steps.app-token.outputs" in text]
        self.assertEqual(len(users), 1)
        path, text = users[0]
        self.assertEqual(path[-2:], ("env", "GH_TOKEN"))
        self.assertEqual(text.strip(), "${{ steps.app-token.outputs.token }}")
        holder = self.jobs["registry-pr"]["steps"][int(path[3])]
        self.assertIn("gh pr create", holder["run"])

    def test_the_pr_job_is_skipped_without_the_credential(self):
        job = self.jobs["registry-pr"]
        self.assertEqual(sorted(job["needs"]), ["credential", "validate"])
        self.assertIn("needs.credential.outputs.present == 'true'", job["if"])
        self.assertEqual(self.jobs["credential"]["outputs"]["present"],
                         "${{ steps.check.outputs.present }}")

    def test_the_pr_job_revalidates_and_builds_before_it_holds_the_credential(self):
        steps = self.jobs["registry-pr"]["steps"]
        names = [s.get("id") for s in steps]
        self.assertIn("revalidate", names)
        holder = next(i for i, s in enumerate(steps)
                      if any("secrets." in v for _, v in strings(s)))
        self.assertEqual(holder, steps.index(self.mint_step()),
                         "the mint step is the first to hold a secret")
        self.assertLess(names.index("revalidate"), holder)
        body = steps[names.index("revalidate")]["run"]
        self.assertIn("--expect-sha", body)
        # The registry's own scripts run in the build step, before the
        # token exists, and that step names no credential at all.
        self.assertLess(names.index("build"), holder)
        build = steps[names.index("build")]
        self.assertIn("improve_gate.py prepare", build["run"])
        self.assertNotIn("GH_TOKEN", json.dumps(build))
        self.assertNotIn("extraheader", build["run"])

    def open_step(self) -> dict:
        return next(s for s in self.jobs["registry-pr"]["steps"]
                    if "gh pr create" in s.get("run", ""))

    def test_opens_one_pull_request_in_acsi_agentskills(self):
        bodies = "\n".join(body for _, body in run_blocks(self.gate))
        creates = re.findall(r"gh pr create[^\n]*(?:\\\n[^\n]*)*", bodies)
        self.assertEqual(len(creates), 1)
        self.assertIn('--repo "$TARGET_REPO" "${draft[@]}"', creates[0])
        step = self.open_step()
        self.assertEqual(step["env"]["TARGET_REPO"],
                         "example-adopter/acsi-agentskills")
        self.assertEqual(step["env"]["MODE"], "${{ steps.build.outputs.mode }}")
        self.assertIn('if [ "$MODE" = draft ]; then\n  draft=(--draft)\nfi',
                      step["run"].replace("          ", ""))
        self.assertIn('-f "labels[]=agent-authored" -f "labels[]=eval-improve"',
                      step["run"])
        for banned in ("--force", "push -f", "--admin", "gh pr review",
                       "--squash", "--rebase", "--approve", "git push --delete"):
            self.assertNotIn(banned, bodies)

    def test_auto_merge_is_a_merge_commit_enabled_only_in_auto_mode(self):
        run = self.open_step()["run"]
        merges = re.findall(r"gh pr merge[^\n]*", run)
        self.assertEqual(merges, ['gh pr merge "$url" --repo "$TARGET_REPO" '
                                  '--auto --merge'])
        auto = run.index('if [ "$MODE" = auto ]; then')
        self.assertLess(auto, run.index("gh pr merge"))
        self.assertLess(run.index("labels[]="), auto,
                        "labels go on before auto-merge is enabled")
        self.assertLess(run.index("gh pr merge"), run.index('"MERGE"'),
                        "the enabled merge method is read back")
        other = "\n".join(body for job, body in run_blocks(self.gate)
                          if body is not run)
        self.assertNotIn("gh pr merge", other)

    def test_one_loop_pull_request_at_a_time(self):
        run = self.open_step()["run"]
        self.assertIn("eval-improve/*|retire-skill/*|draft-skill/*)", run)
        self.assertLess(run.index("retire-skill/*"), run.index("push origin"))
        self.assertLess(run.index("ls-remote --heads"), run.index("push origin"))

    def test_the_policy_file_names_the_mode_the_build_step_reads(self):
        policy = yaml.safe_load(gate.POLICY.read_text(encoding="utf-8"))
        self.assertEqual(policy, {"improve_merge_mode": "auto"})
        build = next(s for s in self.jobs["registry-pr"]["steps"]
                     if s.get("id") == "build")
        self.assertNotIn("--policy", build["run"],
                         "the committed default-branch policy is the one read")

    def test_every_push_targets_the_improve_branch_without_force(self):
        targets = [t for _, body in run_blocks(self.gate)
                   for t in PUSH_TARGET.findall(body)]
        self.assertEqual(targets, ['"HEAD:refs/heads/${TARGET_BRANCH}"'])

    def test_no_concurrency_group(self):
        for doc in (self.gate, self.signal):
            self.assertNotIn("concurrency", doc)
            for job in doc["jobs"].values():
                self.assertNotIn("concurrency", job)

    def test_every_uses_is_locked_and_already_pinned_elsewhere(self):
        pinned = set()
        for other in WORKFLOWS.glob("*.yml"):
            if other not in (GATE, SIGNAL):
                pinned |= {n.value for n in uses_nodes(
                    yaml.compose(other.read_text(encoding="utf-8")))}
        for path in (GATE, SIGNAL):
            text = path.read_text(encoding="utf-8")
            lines = text.splitlines()
            for node in uses_nodes(yaml.compose(text)):
                with self.subTest(path=path.name, uses=node.value):
                    self.assertIsNone(lock_problem(node.value, path))
                    rest = lines[node.end_mark.line][node.end_mark.column:]
                    self.assertEqual(rest.strip(), "",
                                     "a pin carries no trailing comment")
                    self.assertIn(node.value, pinned)

    def test_no_expressions_in_run_blocks(self):
        for doc in (self.gate, self.signal):
            blocks = run_blocks(doc)
            self.assertTrue(blocks)
            for job, body in blocks:
                with self.subTest(job=job, body=body[:40]):
                    self.assertNotIn("${{", body)

    def test_checkouts_are_the_default_branch_without_credentials(self):
        for name in ("validate", "registry-pr"):
            checkouts = [s for s in self.jobs[name]["steps"]
                         if str(s.get("uses", "")).startswith(
                             "actions/checkout@")]
            self.assertEqual(len(checkouts), 1, name)
            self.assertEqual(checkouts[0]["with"]["ref"],
                             "${{ github.event.repository.default_branch }}")
            self.assertIs(checkouts[0]["with"]["persist-credentials"], False)

    def test_the_guards_can_fail(self):
        self.assertIsNotNone(lock_problem("actions/checkout@v4", GATE))
        self.assertEqual(PUSH_TARGET.findall("git -C t push origin HEAD:main"),
                         ["HEAD:main"])
        bad = {"jobs": {"j": {"steps": [{"env": {"T": "${{ secrets.X }}"}}]}}}
        self.assertEqual([p for p, t in strings(bad) if "secrets." in t],
                         [("jobs", "j", "steps", "0", "env", "T")])


class FireCheckTests(unittest.TestCase):
    """`improve_gate.py fire-check`, run by routine-eval-fire.yml before the
    bearer exists: the loop's own fixture resolution (`skill_fixtures`,
    `load_fixtures`, `MIN_FIXTURES`) plus harness/registries.yml, read-only
    and offline. The registry checkout is not in the fire job, so the last
    resolution step (`find_skill_dir`) is left to the routine."""

    def setUp(self):
        # The committed fixtures are the example checkout's (the kit ships
        # none; test/example_evals.py).
        patcher = mock.patch.object(pse, "EVALS_DIR", example_evals.checkout() / "evals")
        patcher.start()
        self.addCleanup(patcher.stop)

    def check(self, skill, holdout=""):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), \
                contextlib.redirect_stdout(io.StringIO()):
            code = gate.main(["fire-check", skill, "--holdout", holdout])
        return code, err.getvalue()

    def assert_rejected(self, skill, message, holdout=""):
        code, err = self.check(skill, holdout)
        self.assertEqual(code, 1, err)
        self.assertEqual(err, f"rejected: {message}\n")
        for value in (skill, holdout):
            if value:
                self.assertNotIn(value, err, "a rejection never echoes the input")

    def test_accepts_a_registry_skill_with_enough_fixtures(self):
        for holdout in ("", "supersede", "bootstrap"):
            with self.subTest(holdout=holdout):
                self.assertEqual(self.check(SKILL, holdout), (0, ""))

    def test_refuses_dirs_that_are_not_one_skill(self):
        # guidance and real-work fixtures name no skill; a flat fixture or a
        # missing directory has no nested fixture for the loop to split.
        for skill in ("guidance", "real-work", "browser-testing",
                      "no-such-skill-xyz", "writing-adr", "harness"):
            with self.subTest(skill=skill):
                self.assert_rejected(skill, gate.FIRE_NOT_A_SKILL)

    def test_refuses_a_skill_of_another_registry(self):
        # Three nested fixtures, but for acsi-agentskills-private, which the
        # routine never checks out.
        self.assertGreaterEqual(len(pse.skill_fixtures("acsi-writing-style")),
                                pse.MIN_FIXTURES)
        self.assert_rejected("acsi-writing-style", gate.FIRE_NOT_ROUTINE_REGISTRY)

    def test_refuses_too_few_fixtures(self):
        # Synthetic layouts, so the case does not move when a real skill
        # gains fixtures; every count below MIN_FIXTURES is refused.
        url, sk = gate.REGISTRY_URL, "zqx-synthetic"
        for count in range(1, pse.MIN_FIXTURES):
            fixtures = {f"{sk}/f{n}": {"skill": sk, "registry": url}
                        for n in range(count)}
            with self.subTest(count=count), self._evals(fixtures):
                self.assert_rejected(sk, gate._fire_too_few())

    def test_refuses_a_holdout_that_is_not_a_fixture_of_the_skill(self):
        for holdout in ("no-such-holdout-xyz", "proposal-bio", "..", "."):
            with self.subTest(holdout=holdout):
                code, err = self.check(SKILL, holdout)
                self.assertEqual((code, err),
                                 (1, f"rejected: {gate.FIRE_BAD_HOLDOUT}\n"))

    def test_messages_are_distinct(self):
        messages = (gate.FIRE_NOT_A_SKILL, gate.FIRE_NOT_ROUTINE_REGISTRY,
                    gate._fire_too_few(), gate.FIRE_BAD_HOLDOUT)
        self.assertEqual(len(set(messages)), len(messages))
        self.assertIn(str(pse.MIN_FIXTURES), gate._fire_too_few())

    def _evals(self, fixtures):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        for rel, doc in fixtures.items():
            (tmp / rel).mkdir(parents=True)
            (tmp / rel / "fixture.yaml").write_text(yaml.safe_dump(doc),
                                                    encoding="utf-8")
        return mock.patch.object(pse, "EVALS_DIR", tmp)

    def test_synthetic_layouts(self):
        url = gate.REGISTRY_URL
        sk = "zqx-synthetic"
        cases = {
            "another_skill_named": ({f"{sk}/{n}": {"skill": "other", "registry": url}
                                     for n in "abc"}, gate.FIRE_NOT_A_SKILL),
            "one_fixture_names_no_skill": (
                {f"{sk}/a": {"skill": sk, "registry": url},
                 f"{sk}/b": {"skill": sk, "registry": url},
                 f"{sk}/c": {"registry": url}}, gate.FIRE_NOT_A_SKILL),
            "unreadable": ({f"{sk}/a": ["not", "a", "mapping"],
                            f"{sk}/b": {"skill": sk, "registry": url},
                            f"{sk}/c": {"skill": sk, "registry": url}},
                           gate.FIRE_NOT_A_SKILL),
            "two_registries": (
                {f"{sk}/a": {"skill": sk, "registry": url},
                 f"{sk}/b": {"skill": sk, "registry": url},
                 f"{sk}/c": {"skill": sk,
                         "registry": "https://github.com/example-adopter/example-site-platform"}},
                gate.FIRE_NOT_A_SKILL),
            "unlisted_registry": ({f"{sk}/{n}": {"skill": sk,
                                              "registry": "https://example.com/r"}
                                   for n in "abc"}, gate.FIRE_NOT_ROUTINE_REGISTRY),
            "no_registry": ({f"{sk}/{n}": {"skill": sk} for n in "abc"},
                            gate.FIRE_NOT_ROUTINE_REGISTRY),
        }
        for label, (fixtures, message) in cases.items():
            with self.subTest(case=label), self._evals(fixtures):
                self.assert_rejected(sk, message)
        with self._evals({f"{sk}/{n}": {"skill": sk, "registry": url}
                          for n in "abc"}):
            self.assertEqual(self.check(sk, "b"), (0, ""))


class ThreePrGateTests(unittest.TestCase):
    """#71 item 5 / ADR 0010 decision 4: no scheduled loop until three
    human-reviewed loop pull requests have merged. Nothing that fires the
    routine or handles an improve branch may run on a schedule."""

    def test_nothing_that_reaches_the_loop_is_scheduled(self):
        reaching = []
        for path in sorted(WORKFLOWS.glob("*.y*ml")):
            text = path.read_text(encoding="utf-8")
            if ROUTINE_ID in text or "eval-improve" in text \
                    or "propose_skill_edit" in text:
                reaching.append(path.name)
                with self.subTest(workflow=path.name):
                    self.assertNotIn("schedule", triggers(load(path)) or {})
        self.assertIn(GATE.name, reaching)
        self.assertIn(SIGNAL.name, reaching)
        self.assertIn("routine-eval-fire.yml", reaching)


if __name__ == "__main__":
    unittest.main()
