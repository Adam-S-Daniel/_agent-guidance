#!/usr/bin/env python3
"""The draft loop: a `new-skill` lesson becomes a measured skill, or a hook.

`scripts/draft_new_skill.py` is every deterministic step around the eval
routine's `draft` mode. What is pinned here:

  * the proposal schema (one table), the selector's order and exclusions,
    and the hook-first decline path (`--hook-declined`, `hook_declined=1`);
  * `drafted-subjects` reads remote branches through git plumbing only and
    prints `<subject> <decision>`;
  * the fire payload: exact keys, `id` kept, dates dropped, the 60000-byte
    cap, and `fire-check` agreeing with it;
  * HOOKS FIRST: `hook-first` writes a record and nothing else, and the gate
    accepts that branch, refuses one that also adds fixtures or a patch, and
    refuses a skill record without `triage`;
  * `measure` through a fake Runner: the decision rule (both arms complete,
    one green with_skill fixture, summed with_skill pass rate strictly
    above without_skill's), the record, and the branch-ready results tree;
  * `validate` on a real git branch, never quoting the branch;
  * `apply` against a copy of the sibling acsi-agentskills checkout, then
    the registry's own README and version checks, and `fixtures-apply`
    satisfying this repo's README layout rule;
  * both PR bodies and the issue body: marker line last, no closing
    keyword, the issue's one JSON block with exactly its keys.

Every fixture is built in-test in temporary directories (example.com only).
Discovered and run by test/run_tests.py.
"""

from __future__ import annotations

import contextlib
import importlib.util
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

TEST_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = TEST_DIR.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "harness"))

import draft_new_skill as dns  # noqa: E402
import run_eval  # noqa: E402

RUN_ID = "20261026T143000Z-0a1b2c"
BRANCH = f"claude/draft-skill-{RUN_ID}"
SUBJECT = "lint-before-push"
PLUGIN = "acsi-coding-anywhere"
SHA = "2" * 40
MARKER = "do-not-echo-this-marker"
FIXTURE_NAMES = ("alpha", "bravo", "charlie", "delta")

GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.com",
    "GIT_COMMITTER_NAME": "fixture",
    "GIT_COMMITTER_EMAIL": "fixture@example.com",
    "GIT_AUTHOR_DATE": "2026-10-26T00:00:00Z",
    "GIT_COMMITTER_DATE": "2026-10-26T00:00:00Z",
    "GIT_CONFIG_COUNT": "2",
    "GIT_CONFIG_KEY_0": "gc.auto", "GIT_CONFIG_VALUE_0": "0",
    "GIT_CONFIG_KEY_1": "maintenance.auto", "GIT_CONFIG_VALUE_1": "false",
}


def git(repo: Path, *args: str) -> str:
    # Raw git is fine here (#343): a throwaway fixture repository.
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True,
                          env={**os.environ, **GIT_ENV}).stdout.strip()


def proposal(**changes) -> dict:
    doc = {"schema_version": 1, "id": "lesson:0001", "subject": SUBJECT,
           "kind": "new-skill", "evidence_count": 3,
           "first_seen": "2026-10-20", "last_seen": "2026-10-24",
           "examples": ["An agent pushed a branch that failed lint at "
                        "ci.example.com, twice in one session."],
           "repos": ["example-org/example-repo"]}
    doc.update(changes)
    return doc


def payload_doc(**changes) -> dict:
    doc = {"run_id": RUN_ID, "mode": "draft",
           "proposal": {k: proposal()[k] for k in dns.PAYLOAD_PROPOSAL_KEYS}}
    doc.update(changes)
    return doc


def sketch(**changes) -> dict:
    doc = {"event": "pre-push",
           "inspects": "Runs the repository's lint command on the pushed range.",
           "refusal_message": "Lint fails on this push; fix it first.",
           "rationale": "A push-time check catches every case; a skill relies "
                        "on the agent remembering.",
           "false_positive_risk": "Low: the lint command is the one CI runs."}
    doc.update(changes)
    return doc


SKILL_MD = (f"---\nname: {SUBJECT}\ndescription: Run the linter before every "
            "push.\n---\n\n# Lint before push\n\nRun the linter first.\n")
PURPOSE_MD = "# Purpose\n\nInvented motivating pattern for the test.\n"


def fixture_yaml(subject=SUBJECT, **changes) -> str:
    doc = {"skill": subject, "registry": dns.REGISTRY_URL,
           "prompt": "Push the change in this repository.",
           "arms": {"with_skill": {"install": "copy"},
                    "without_skill": {"install": "none"}},
           "judge_rubric": "Did it lint first?",
           "objective_checks": [{"id": "lint-ran", "type": "file_matches",
                                 "paths": ["LOG.md"], "must_match": ["lint"]}]}
    doc.update(changes)
    return yaml.safe_dump(doc, sort_keys=False)


def skill_files() -> dict[str, bytes]:
    return {"SKILL.md": SKILL_MD.encode(), "PURPOSE.md": PURPOSE_MD.encode(),
            "references/checklist.md": b"# Checklist\n\n- lint\n"}


def fixture_files() -> dict[str, dict[str, bytes]]:
    return {name: {"fixture.yaml": fixture_yaml().encode(),
                   "seed/README.md": b"# Example project\n"}
            for name in FIXTURE_NAMES}


def arm(passed: int, total: int = 4, errors: int = 0, tokens=1000.0) -> dict:
    return {"n": 1, "errors": errors, "passed": passed, "total": total,
            "pass_rate": passed / total if total else None, "tokens": tokens}


def rows(with_passed=(4, 3, 2, 2), without_passed=(1, 1, 1, 1)) -> dict:
    return {name: {"run_eval_exit": 0, "with_skill": arm(w),
                   "without_skill": arm(o)}
            for name, w, o in zip(FIXTURE_NAMES, with_passed, without_passed)}


def skill_record(**changes) -> dict:
    doc = {"schema": 1, "run_id": RUN_ID, "decision": "accepted",
           "reasons": ["every fixture measured"], "subject": SUBJECT,
           "plugin": PLUGIN, "registry": dns.REGISTRY_URL, "registry_sha": SHA,
           "evidence_count": 3, "trials": 1,
           "triage": {"mechanical": False,
                      "reason": "Whether a change is ready to push is a judgment."},
           "fixtures": rows(), "skill_creator_commit": dns.SKILL_CREATOR_COMMIT,
           "tokens_basis": dns.TOKENS_BASIS}
    doc.update(changes)
    return doc


def hook_record(**changes) -> dict:
    doc = {"schema": 1, "run_id": RUN_ID, "decision": "hook-first",
           "subject": SUBJECT, "evidence_count": 3,
           "proposal": payload_doc()["proposal"], "check_sketch": sketch()}
    doc.update(changes)
    return doc


def run_main(*argv: str, runner=None) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = dns.main(list(argv), runner=runner)
    return code, out.getvalue(), err.getvalue()


def outputs(text: str) -> dict:
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="test-draft-skill-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def write(self, path: Path, data) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, (dict, list)):
            data = json.dumps(data)
        path.write_bytes(data if isinstance(data, bytes) else data.encode())
        return path


# ---------------------------------------------------------------------------
# Proposal schema, select, drafted-subjects.

class ProposalSchema(unittest.TestCase):
    def test_a_well_formed_proposal_passes(self):
        dns.check_proposal(proposal(), "2026-10-26", SUBJECT)

    def test_each_malformed_field_is_refused(self):
        bad = {
            "extra key": {**proposal(), "note": "x"},
            "missing key": {k: v for k, v in proposal().items() if k != "repos"},
            "subject shape": proposal(subject="Bad_Subject"),
            "kind": proposal(kind="skill-edit"),
            "bool count": proposal(evidence_count=True),
            "zero count": proposal(evidence_count=0),
            "no examples": proposal(examples=[]),
            "long example": proposal(examples=["x" * 2001]),
            "21 examples": proposal(examples=["x"] * 21),
            "control char": proposal(examples=["a\x1bb"]),
            "repo shape": proposal(repos=["not a repo"]),
            "bad date": proposal(first_seen="2026-02-30"),
            "dates reversed": proposal(first_seen="2026-10-25"),
            "schema version": proposal(schema_version=2),
        }
        for label, doc in bad.items():
            with self.subTest(label), self.assertRaises(dns.Rejected):
                dns.check_proposal(doc)

    def test_the_path_must_agree_with_the_content(self):
        with self.assertRaises(dns.Rejected):
            dns.check_proposal(proposal(), "2026-10-23", SUBJECT)
        with self.assertRaises(dns.Rejected):
            dns.check_proposal(proposal(), "2026-10-26", "other-subject")

    def test_the_schema_is_one_table_naming_every_key(self):
        self.assertEqual(set(dns.PROPOSAL_SCHEMA), set(proposal()))
        self.assertLessEqual(set(dns.PAYLOAD_PROPOSAL_KEYS), set(dns.PROPOSAL_SCHEMA))


class Select(Case):
    def setUp(self):
        super().setUp()
        self.lessons = self.tmp / "lessons"
        self.registry = self.tmp / "registry"
        self.registry.mkdir()
        self.evals = self.tmp / "evals"
        self.evals.mkdir()
        self.drafted = self.write(self.tmp / "drafted.txt", "")
        self.declined = self.write(self.tmp / "declined.txt", "")

    def put(self, day: str, doc: dict) -> str:
        rel = f"proposals/{day}/{doc['subject']}__new-skill.json"
        self.write(self.lessons / rel, doc)
        return rel

    def select(self, *extra: str) -> dict:
        code, out, err = run_main(
            "select", "--lessons", str(self.lessons), "--registry",
            f"acsi-agentskills={self.registry}", "--evals", str(self.evals),
            "--drafted", str(self.drafted), "--hook-declined", str(self.declined),
            *extra)
        self.assertEqual(code, 0, err)
        self.assertNotIn(MARKER, out + err)
        return outputs(out)

    def test_highest_evidence_then_newest_then_name(self):
        self.put("2026-10-26", proposal(subject="aaa", evidence_count=2))
        best = self.put("2026-10-26", proposal(subject="zzz", evidence_count=5))
        self.assertEqual(self.select(), {"proposal": best, "count": "1",
                                         "hook_declined": "0"})
        newer = self.put("2026-10-26", proposal(subject="yyy", evidence_count=5,
                                                last_seen="2026-10-25"))
        self.assertEqual(self.select()["proposal"], newer)
        same = self.put("2026-10-26", proposal(subject="xxx", evidence_count=5,
                                               last_seen="2026-10-25"))
        self.assertEqual(self.select()["proposal"], same)

    def test_invalid_proposals_are_skipped_and_never_printed(self):
        self.put("2026-10-26", proposal(subject="bad", examples=[MARKER] * 21))
        self.write(self.lessons / "proposals/2026-10-26/odd__new-skill.json",
                   MARKER + "{not json")
        self.assertEqual(self.select()["count"], "0")

    def test_a_proposal_too_big_to_fire_is_never_picked(self):
        self.put("2026-10-26", proposal(examples=["é" * 2000] * 20))
        self.assertEqual(self.select()["count"], "0")

    def test_symlinked_proposals_are_ignored(self):
        real = self.write(self.tmp / "elsewhere.json", proposal())
        (self.lessons / "proposals/2026-10-26").mkdir(parents=True)
        (self.lessons / f"proposals/2026-10-26/{SUBJECT}__new-skill.json").symlink_to(real)
        self.assertEqual(self.select()["count"], "0")

    def test_existing_skills_evals_and_drafts_are_excluded(self):
        self.put("2026-10-26", proposal())
        self.assertEqual(self.select()["count"], "1")
        cases = {
            "registry": lambda: self.write(
                self.registry / f"plugins/{PLUGIN}/skills/{SUBJECT}/SKILL.md", "x"),
            "evals": lambda: (self.evals / SUBJECT).mkdir(),
            "accepted": lambda: self.write(self.drafted, f"{SUBJECT} accepted\n"),
            "rejected": lambda: self.write(self.drafted, f"{SUBJECT} rejected\n"),
            "hook-first": lambda: self.write(self.drafted, f"{SUBJECT} hook-first\n"),
        }
        for label, make in cases.items():
            with self.subTest(label):
                make()
                self.assertEqual(self.select()["count"], "0")
                shutil.rmtree(self.registry / "plugins", ignore_errors=True)
                shutil.rmtree(self.evals / SUBJECT, ignore_errors=True)
                self.write(self.drafted, "")

    def test_a_declined_hook_proposal_makes_the_subject_eligible_again(self):
        rel = self.put("2026-10-26", proposal())
        self.write(self.drafted, f"{SUBJECT} hook-first\n")
        self.write(self.declined, f"{SUBJECT}\n")
        self.assertEqual(self.select(), {"proposal": rel, "count": "1",
                                         "hook_declined": "1"})
        # A skill record after the decline is final.
        self.write(self.drafted, f"{SUBJECT} hook-first\n{SUBJECT} rejected\n")
        self.assertEqual(self.select()["count"], "0")

    def test_eligibility_is_a_pure_rule(self):
        self.assertEqual(dns.eligibility("a", {}, set()), (True, False))
        self.assertEqual(dns.eligibility("a", {"a": {"hook-first"}}, set()), (False, False))
        self.assertEqual(dns.eligibility("a", {"a": {"hook-first"}}, {"a"}), (True, True))
        self.assertEqual(dns.eligibility("a", {"a": {"accepted"}}, {"a"}), (False, False))


class DraftedSubjects(Case):
    def test_reads_remote_draft_branches_through_plumbing(self):
        repo = self.tmp / "repo"
        repo.mkdir()
        git(repo, "init", "-q", "-b", "main")
        self.write(repo / "README.md", "x\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "base")
        base = git(repo, "rev-parse", "HEAD")
        branches = {
            "20261026T000000Z-aaaaaa": skill_record(run_id="x", subject="one-thing"),
            "20261026T000000Z-bbbbbb": hook_record(subject="two-thing"),
            "20261026T000000Z-cccccc": {"subject": "Bad Subject", "decision": "accepted"},
            "20261026T000000Z-dddddd": MARKER,
        }
        for run_id, doc in branches.items():
            git(repo, "checkout", "-q", "-b", f"b-{run_id}", base)
            self.write(repo / f"draft-skill/{run_id}/summary.json",
                       doc if isinstance(doc, dict) else doc)
            git(repo, "add", "-A")
            git(repo, "commit", "-q", "-m", "draft")
            git(repo, "update-ref", f"refs/remotes/origin/claude/draft-skill-{run_id}",
                "HEAD")
        git(repo, "update-ref", "refs/remotes/origin/claude/draft-skill-nope", base)
        git(repo, "checkout", "-q", "main")
        code, out, err = run_main("drafted-subjects", "--repo", str(repo))
        self.assertEqual(code, 0, err)
        self.assertEqual(out.splitlines(), ["one-thing accepted", "two-thing hook-first"])
        self.assertNotIn(MARKER, out + err)


# ---------------------------------------------------------------------------
# payload / fire-check / hook-first.

class Payload(Case):
    def setUp(self):
        super().setUp()
        self.lessons = self.tmp / "lessons"
        self.rel = f"proposals/2026-10-26/{SUBJECT}__new-skill.json"
        self.write(self.lessons / self.rel, proposal())

    def test_the_payload_keeps_id_and_drops_dates(self):
        code, out, err = run_main("payload", "--lessons", str(self.lessons),
                                  "--proposal", self.rel, "--run-id", RUN_ID)
        self.assertEqual(code, 0, err)
        doc = json.loads(out)
        self.assertEqual(list(doc), ["run_id", "mode", "proposal"])
        self.assertEqual(set(doc["proposal"]),
                         {"id", "subject", "evidence_count", "examples", "repos"})
        self.assertEqual(doc["proposal"]["id"], "lesson:0001")
        code, out, _ = run_main("payload", "--lessons", str(self.lessons),
                                "--proposal", self.rel, "--run-id", RUN_ID,
                                "--hook-declined")
        self.assertIs(json.loads(out)["hook_declined"], True)

    def test_bad_paths_and_run_ids_are_refused(self):
        outside = self.write(self.tmp / "outside.json", proposal())
        link = self.lessons / f"proposals/2026-10-25/{SUBJECT}__new-skill.json"
        link.parent.mkdir(parents=True)
        link.symlink_to(outside)
        for rel, run_id in ((f"proposals/2026-10-25/{SUBJECT}__new-skill.json", RUN_ID),
                            ("../outside.json", RUN_ID),
                            (f"proposals/2026-10-26/{SUBJECT}__skill-edit.json", RUN_ID),
                            (self.rel, "not-a-run-id")):
            with self.subTest(rel=rel, run_id=run_id):
                code, out, _ = run_main("payload", "--lessons", str(self.lessons),
                                        "--proposal", rel, "--run-id", run_id)
                self.assertEqual(code, 1)
                self.assertEqual(out, "")

    def test_fire_check_matches_the_payload_contract(self):
        good = {"plain": payload_doc(), "declined": payload_doc(hook_declined=True)}
        bad = {"declined false": payload_doc(hook_declined=False),
               "extra key": payload_doc(note=1),
               "mode": payload_doc(mode="eval"),
               "run id": payload_doc(run_id="x"),
               "dates kept": payload_doc(proposal={**payload_doc()["proposal"],
                                                   "first_seen": "2026-10-20"}),
               "subject": payload_doc(proposal={**payload_doc()["proposal"],
                                                "subject": "../x"}),
               "too big": payload_doc(proposal={**payload_doc()["proposal"],
                                                "examples": ["é" * 2000] * 20})}
        for label, doc in [*good.items(), *bad.items()]:
            with self.subTest(label):
                path = self.write(self.tmp / f"{label}.json", doc)
                code, out, err = run_main("fire-check", "--payload", str(path))
                self.assertEqual(code, 0 if label in good else 1, err)
                self.assertEqual(out, "")


class HookFirst(Case):
    def run_hook(self, payload=None, sketch_doc=None):
        p = self.write(self.tmp / "payload.json", payload or payload_doc())
        s = self.write(self.tmp / "sketch.json", sketch_doc or sketch())
        return run_main("hook-first", "--payload", str(p), "--proposal-json", str(s),
                        "--results", str(self.tmp / "results"))

    def test_writes_a_hook_first_record_and_report_only(self):
        code, out, err = self.run_hook()
        self.assertEqual(code, 0, err)
        root = self.tmp / "results"
        self.assertEqual(sorted(p.relative_to(root).as_posix() for p in root.rglob("*")
                                if p.is_file()),
                         [f"draft-skill/{RUN_ID}/report.md",
                          f"draft-skill/{RUN_ID}/summary.json"])
        record = json.loads((root / f"draft-skill/{RUN_ID}/summary.json").read_text())
        self.assertEqual(record, hook_record())
        dns.check_record(record, RUN_ID)
        report = (root / f"draft-skill/{RUN_ID}/report.md").read_text()
        self.assertTrue(report.startswith(f"# Hook first: {SUBJECT}\n"))
        self.assertNotIn("ci.example.com", report)

    def test_refuses_a_declined_lesson_and_a_bad_sketch(self):
        for label, kwargs in {
                "declined": {"payload": payload_doc(hook_declined=True)},
                "event": {"sketch_doc": sketch(event="commit-msg")},
                "mention": {"sketch_doc": sketch(rationale="ask @someone")},
                "fence": {"sketch_doc": sketch(inspects="```sh\nx\n```")},
                "long": {"sketch_doc": sketch(refusal_message="x" * 501)},
                "keys": {"sketch_doc": {**sketch(), "target": "x"}}}.items():
            with self.subTest(label):
                shutil.rmtree(self.tmp / "results", ignore_errors=True)
                code, _, _ = self.run_hook(**kwargs)
                self.assertEqual(code, 2)
                self.assertFalse((self.tmp / "results").exists())


# ---------------------------------------------------------------------------
# measure.

class FakeRunner:
    """Writes the summaries run_eval would, from a table of objective results
    per fixture and arm: (passed, total) or None for no summary."""

    def __init__(self, table: dict, codes: dict | None = None):
        self.table, self.codes, self.calls = table, codes or {}, []

    def run_eval(self, argv: list[str]) -> int:
        self.calls.append(argv)
        fixture = argv[argv.index("--fixture") + 1]
        trials = int(argv[argv.index("--trials") + 1])
        runs = Path(argv[argv.index("--results-dir") + 1])
        ts = argv[argv.index("--timestamp") + 1]
        skill = yaml.safe_load((Path(argv[0]) / fixture / "fixture.yaml").read_text())["skill"]
        for arm_name, result in self.table[fixture].items():
            if result is None:
                continue
            passed, total = result
            checks = [{"id": f"c{i}", "passed": i < passed} for i in range(total)]
            summary = {"error": None, "objective_checks": checks,
                       "model_tokens": {"claude-x": {
                           "input_tokens": 100, "output_tokens": 20,
                           "cache_read_input_tokens": 0,
                           "cache_creation_input_tokens": 0}},
                       "cross_model": {"complete": True}}
            arm_dir = runs / skill / ts / fixture / arm_name
            dirs = [arm_dir] if trials == 1 else [arm_dir / f"trial-{k}"
                                                  for k in range(1, trials + 1)]
            for d in dirs:
                d.mkdir(parents=True, exist_ok=True)
                (d / "summary.json").write_text(json.dumps(summary))
        return self.codes.get(fixture, 0)


def table(with_passed=(4, 3, 2, 2), without_passed=(1, 1, 1, 1)) -> dict:
    return {name: {"with_skill": (w, 4), "without_skill": (o, 4)}
            for name, w, o in zip(FIXTURE_NAMES, with_passed, without_passed)}


class Measure(Case):
    def setUp(self):
        super().setUp()
        self.registry = self.tmp / "registry"
        self.write(self.registry / f"plugins/{PLUGIN}/skills/other/SKILL.md",
                   "---\nname: other\ndescription: x\n---\n")
        self.write(self.registry / "README.md", "# Registry\n")
        git(self.registry, "init", "-q", "-b", "main")
        git(self.registry, "add", "-A")
        git(self.registry, "commit", "-q", "-m", "registry")
        self.sha = git(self.registry, "rev-parse", "HEAD")
        self.staging = self.tmp / "staging"
        for rel, data in skill_files().items():
            self.write(self.staging / "skill" / rel, data)
        for name, files in fixture_files().items():
            for rel, data in files.items():
                self.write(self.staging / "fixtures" / name / rel, data)
        self.write(self.staging / "plugin", PLUGIN + "\n")
        self.write(self.staging / "triage.json",
                   {"mechanical": False, "reason": "Readiness is a judgment call."})
        self.results = self.tmp / "results"

    def measure(self, runner, payload=None, trials=1):
        p = self.write(self.tmp / "payload.json", payload or payload_doc())
        return run_main("measure", "--staging", str(self.staging), "--registry",
                        f"acsi-agentskills={self.registry}", "--ref", self.sha,
                        "--payload", str(p), "--trials", str(trials),
                        "--results", str(self.results), runner=runner)

    def record(self) -> dict:
        return json.loads((self.results / f"draft-skill/{RUN_ID}/summary.json").read_text())

    def files(self) -> list[str]:
        return sorted(p.relative_to(self.results).as_posix()
                      for p in self.results.rglob("*") if p.is_file())

    def test_an_accepted_draft_writes_the_branch_tree(self):
        runner = FakeRunner(table())
        code, _, err = self.measure(runner, trials=2)
        self.assertEqual(code, 0, err)
        record = self.record()
        self.assertEqual(record["decision"], "accepted")
        self.assertEqual(set(record), set(dns.SKILL_RECORD_KEYS))
        self.assertEqual(record["fixtures"]["alpha"]["with_skill"]["pass_rate"], 1.0)
        self.assertEqual(record["fixtures"]["alpha"]["without_skill"]["tokens"], 120.0)
        self.assertEqual(record["skill_creator_commit"],
                         "2a40fd2e7c52207aa903bd33fc4c65716126966e")
        dns.check_record(record, RUN_ID)
        expected = [f"draft-skill/{RUN_ID}/{n}" for n in
                    ("report.md", "skill.patch", "summary.json")]
        expected += [f"evals/{SUBJECT}/{n}/{f}" for n in FIXTURE_NAMES
                     for f in ("fixture.yaml", "seed/README.md")]
        self.assertEqual(self.files(), sorted(expected))
        patch = (self.results / f"draft-skill/{RUN_ID}/skill.patch").read_text()
        self.assertEqual(dns.parse_new_files_patch(
            patch, dns.skill_prefix(PLUGIN, SUBJECT)), skill_files())
        report = (self.results / f"draft-skill/{RUN_ID}/report.md").read_text()
        self.assertTrue(report.startswith(f"# New skill: {SUBJECT}\n"))
        self.assertIn("Drafted from a redacted lesson with 3 pieces of evidence", report)
        self.assertNotIn("ci.example.com", report)
        self.assertIsNone(dns.text_problem(report))
        # The routine's eval-mode flags, both arms, per fixture.
        self.assertEqual(len(runner.calls), 4)
        argv = runner.calls[0]
        for flag, value in (("--arm", "both"), ("--trials", "2"),
                            ("--run-billing", "subscription"),
                            ("--run-runner", "routine"), ("--run-location", "cloud"),
                            ("--run-id", RUN_ID), ("--fixture", "alpha")):
            self.assertEqual(argv[argv.index(flag) + 1], value)
        self.assertTrue(argv[argv.index("--registry") + 1].startswith("acsi-agentskills="))
        self.assertIn(str(self.staging.resolve()), argv)

    def test_rejections_write_the_record_alone(self):
        cases = {
            "no gain": FakeRunner(table(without_passed=(4, 3, 2, 2))),
            "no green": FakeRunner(table(with_passed=(3, 3, 3, 3))),
            "missing arm": FakeRunner({**table(), "alpha": {"with_skill": (4, 4),
                                                            "without_skill": None}}),
            "run_eval failed": FakeRunner(table(), codes={"bravo": 2}),
        }
        for label, runner in cases.items():
            with self.subTest(label):
                shutil.rmtree(self.results, ignore_errors=True)
                code, _, err = self.measure(runner)
                self.assertEqual(code, 1, err)
                self.assertEqual(self.files(), [f"draft-skill/{RUN_ID}/summary.json"])
                record = self.record()
                self.assertEqual(record["decision"], "rejected")
                dns.check_record(record, RUN_ID)

    def test_bad_staging_is_a_rejected_record_before_any_run(self):
        breaks = {
            "context block": lambda: self.write(
                self.staging / "fixtures/alpha/fixture.yaml",
                fixture_yaml(context={"repository": "example-org/example-repo"})),
            "wrong skill": lambda: self.write(
                self.staging / "fixtures/alpha/fixture.yaml", fixture_yaml("other")),
            "three fixtures": lambda: shutil.rmtree(self.staging / "fixtures/delta"),
            "no purpose": lambda: (self.staging / "skill/PURPOSE.md").unlink(),
            "name mismatch": lambda: self.write(self.staging / "skill/SKILL.md",
                                                SKILL_MD.replace(SUBJECT, "other")),
            "stray file": lambda: self.write(self.staging / "skill/notes.txt", "x\n"),
            "bad plugin": lambda: self.write(self.staging / "plugin", "example-site-platform\n"),
            "verbatim example": lambda: self.write(
                self.staging / "skill/references/checklist.md",
                proposal()["examples"][0] + "\n"),
        }
        for label, make in breaks.items():
            with self.subTest(label):
                shutil.rmtree(self.staging)
                self.setUp_staging()
                shutil.rmtree(self.results, ignore_errors=True)
                make()
                runner = FakeRunner(table())
                code, _, err = self.measure(runner)
                self.assertEqual(code, 1, err)
                self.assertEqual(runner.calls, [])
                self.assertEqual(self.record()["decision"], "rejected")

    def setUp_staging(self):
        for rel, data in skill_files().items():
            self.write(self.staging / "skill" / rel, data)
        for name, files in fixture_files().items():
            for rel, data in files.items():
                self.write(self.staging / "fixtures" / name / rel, data)
        self.write(self.staging / "plugin", PLUGIN + "\n")
        self.write(self.staging / "triage.json",
                   {"mechanical": False, "reason": "Readiness is a judgment call."})

    def test_a_missing_triage_is_refused_unless_the_hook_was_declined(self):
        (self.staging / "triage.json").unlink()
        code, _, _ = self.measure(FakeRunner(table()))
        self.assertEqual(code, 2)
        self.assertFalse(self.results.exists())
        code, _, err = self.measure(FakeRunner(table()),
                                    payload=payload_doc(hook_declined=True))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.record()["triage"],
                         {"mechanical": False, "reason": "declined by the agent-checks lane"})

    def test_an_existing_skill_in_the_registry_is_rejected(self):
        self.write(self.registry / f"plugins/{PLUGIN}/skills/{SUBJECT}/SKILL.md", "x\n")
        git(self.registry, "add", "-A")
        git(self.registry, "commit", "-q", "-m", "taken")
        self.sha = git(self.registry, "rev-parse", "HEAD")
        code, _, _ = self.measure(FakeRunner(table()))
        self.assertEqual(code, 1)


class Decide(unittest.TestCase):
    def test_accepts_only_complete_green_and_better(self):
        self.assertEqual(dns.decide(rows())[0], "accepted")
        self.assertEqual(dns.decide({})[0], "rejected")
        self.assertEqual(dns.decide(rows(with_passed=(3, 3, 3, 3)))[0], "rejected")
        self.assertEqual(dns.decide(rows(without_passed=(4, 3, 2, 2)))[0], "rejected")
        errored = rows()
        errored["alpha"]["without_skill"] = arm(1, errors=1)
        self.assertEqual(dns.decide(errored)[0], "rejected")
        empty = rows()
        empty["alpha"]["with_skill"] = arm(0, total=0)
        self.assertEqual(dns.decide(empty)[0], "rejected")

    def test_pass_rates_compare_exactly(self):
        # Three 1/10s sum to 0.30000000000000004 in floats, above a 3/10, so a
        # float comparison would say this skill adds something when it adds
        # exactly nothing.
        tenths = {f"f{i}": {"run_eval_exit": 0, "with_skill": arm(1, 10),
                            "without_skill": arm(3 if i == 0 else 0, 10)}
                  for i in range(3)}
        self.assertGreater(sum(r["with_skill"]["pass_rate"] for r in tenths.values()),
                           sum(r["without_skill"]["pass_rate"] for r in tenths.values()))
        decision, reasons = dns.decide(tenths)
        self.assertEqual(decision, "rejected")
        self.assertTrue(any(r.startswith("the skill adds nothing") for r in reasons),
                        reasons)


# ---------------------------------------------------------------------------
# validate (the gate).

class Validate(Case):
    def setUp(self):
        super().setUp()
        self.repo = self.tmp / "evals-repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        self.write(self.repo / "README.md", "base\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        self.registry = self.tmp / "agentskills"
        (self.registry / "plugins" / PLUGIN / "skills").mkdir(parents=True)

    def accepted_files(self) -> dict[str, bytes]:
        prefix = f"draft-skill/{RUN_ID}/"
        record = skill_record()
        report = dns.skill_report(record)
        files = {prefix + "summary.json": json.dumps(record).encode(),
                 prefix + "report.md": report.encode(),
                 prefix + "skill.patch": dns.new_files_patch(
                     dns.skill_prefix(PLUGIN, SUBJECT), skill_files()).encode()}
        for name, fx in fixture_files().items():
            for rel, data in fx.items():
                files[f"evals/{SUBJECT}/{name}/{rel}"] = data
        return files

    def hook_files(self) -> dict[str, bytes]:
        prefix = f"draft-skill/{RUN_ID}/"
        return {prefix + "summary.json": json.dumps(hook_record()).encode(),
                prefix + "report.md": dns.hook_report(SUBJECT, 3, sketch()).encode()}

    def branch(self, files: dict[str, bytes], executable=(), name=BRANCH) -> None:
        git(self.repo, "checkout", "-q", "-B", name, "main")
        for path, data in files.items():
            self.write(self.repo / path, data)
            if path in executable:
                (self.repo / path).chmod(0o755)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "draft")
        git(self.repo, "checkout", "-q", "main")

    def validate(self, branch=BRANCH, registry=True):
        out = self.tmp / "out"
        shutil.rmtree(out, ignore_errors=True)
        args = ["validate", "--repo", str(self.repo), "--base", "main",
                "--source", branch, "--branch", branch, "--out", str(out)]
        if registry:
            args += ["--registry", f"acsi-agentskills={self.registry}"]
        code, stdout, err = run_main(*args)
        self.assertNotIn(MARKER, stdout + err)
        return code, outputs(stdout), err

    def test_a_well_formed_accepted_branch_is_staged(self):
        self.branch(self.accepted_files())
        code, out, err = self.validate()
        self.assertEqual(code, 0, err)
        self.assertEqual({k: out[k] for k in ("decision", "subject", "plugin", "run_id")},
                         {"decision": "accepted", "subject": SUBJECT,
                          "plugin": PLUGIN, "run_id": RUN_ID})
        staged = self.tmp / "out"
        self.assertTrue((staged / "skill.patch").is_file())
        self.assertTrue((staged / f"evals/{SUBJECT}/alpha/fixture.yaml").is_file())

    def test_a_well_formed_hook_first_branch_is_staged(self):
        self.branch(self.hook_files())
        code, out, err = self.validate(registry=False)
        self.assertEqual(code, 0, err)
        self.assertEqual(out["decision"], "hook-first")
        self.assertEqual(out["plugin"], "")

    def test_a_rejected_record_alone_passes(self):
        record = skill_record(decision="rejected", plugin=None, fixtures={},
                              reasons=["the skill adds nothing"])
        self.branch({f"draft-skill/{RUN_ID}/summary.json": json.dumps(record).encode()})
        code, out, err = self.validate(registry=False)
        self.assertEqual(code, 0, err)
        self.assertEqual(out["decision"], "rejected")

    def assert_rejected(self, files, **kwargs):
        self.branch(files, **{k: v for k, v in kwargs.items() if k != "registry"})
        code, _, err = self.validate(registry=kwargs.get("registry", True))
        self.assertEqual(code, 1, err)
        self.assertFalse((self.tmp / "out").exists() and any((self.tmp / "out").iterdir()))

    def test_hook_first_with_fixtures_or_a_patch_is_rejected(self):
        accepted = self.accepted_files()
        with_fixture = {**self.hook_files(), f"evals/{SUBJECT}/alpha/fixture.yaml":
                        accepted[f"evals/{SUBJECT}/alpha/fixture.yaml"]}
        with_patch = {**self.hook_files(), f"draft-skill/{RUN_ID}/skill.patch":
                      accepted[f"draft-skill/{RUN_ID}/skill.patch"]}
        for label, files in (("fixtures", with_fixture), ("patch", with_patch)):
            with self.subTest(label):
                self.assert_rejected(files)

    def test_a_skill_record_without_triage_is_rejected(self):
        files = self.accepted_files()
        record = skill_record()
        del record["triage"]
        files[f"draft-skill/{RUN_ID}/summary.json"] = json.dumps(record).encode()
        self.assert_rejected(files)
        record = skill_record(triage={"mechanical": True, "reason": "x"})
        files[f"draft-skill/{RUN_ID}/summary.json"] = json.dumps(record).encode()
        self.assert_rejected(files)

    def test_each_branch_defect_is_rejected_without_echo(self):
        prefix = f"draft-skill/{RUN_ID}/"
        patch_path = prefix + "skill.patch"
        good = self.accepted_files()

        def change(**edits):
            files = dict(good)
            for key, value in edits.items():
                if value is None:
                    files.pop(key)
                else:
                    files[key] = value
            return files

        stray_patch = dns.new_files_patch(f"plugins/{PLUGIN}/skills/other",
                                          skill_files())
        cases = {
            "rejected plus files": {**good, prefix + "summary.json": json.dumps(
                skill_record(decision="rejected", plugin=None)).encode()},
            "stray path": {**good, f"{MARKER}.txt": b"x\n"},
            "other subject's evals": {**good, f"evals/other/alpha/fixture.yaml": b"x\n"},
            "report mention": change(**{prefix + "report.md":
                                        (dns.skill_report(skill_record())
                                         + "\nThanks @someone\n").encode()}),
            "report closes": change(**{prefix + "report.md":
                                       (dns.skill_report(skill_record())
                                        + "\nFixes #12\n").encode()}),
            "patch elsewhere": change(**{patch_path: stray_patch.encode()}),
            "patch miscounted": change(**{patch_path: good[patch_path].replace(
                b"@@ -0,0 +1,", b"@@ -0,0 +1,9")}),
            "patch modifies": change(**{patch_path: good[patch_path].replace(
                b"new file mode 100644\n--- /dev/null", b"index 1..2\n--- a/x", 1)}),
            "no fixtures": {k: v for k, v in good.items() if not k.startswith("evals/")},
            "fixture names other skill": change(**{
                f"evals/{SUBJECT}/alpha/fixture.yaml": fixture_yaml("other").encode()}),
            "fixture has context": change(**{
                f"evals/{SUBJECT}/alpha/fixture.yaml": fixture_yaml(
                    context={"repository": "example-org/x"}).encode()}),
            "figures do not accept": change(**{prefix + "summary.json": json.dumps(
                skill_record(fixtures=rows(without_passed=(4, 4, 4, 4)))).encode()}),
            "wrong run id": change(**{prefix + "summary.json": json.dumps(
                skill_record(run_id="20261026T000000Z-ffffff")).encode()}),
            "binary": change(**{f"evals/{SUBJECT}/alpha/seed/README.md": b"\xff\xfe"}),
            "missing summary": change(**{prefix + "summary.json": None}),
        }
        for label, files in cases.items():
            with self.subTest(label):
                self.assert_rejected(files)
        with self.subTest("executable"):
            self.assert_rejected(good, executable=(f"evals/{SUBJECT}/alpha/seed/README.md",))

    def test_an_existing_subject_is_rejected(self):
        with self.subTest("registry"):
            (self.registry / "plugins" / "acsi-coding-local" / "skills" / SUBJECT).mkdir(
                parents=True)
            self.assert_rejected(self.accepted_files())
            shutil.rmtree(self.registry / "plugins" / "acsi-coding-local")
        with self.subTest("no registry checkout"):
            self.assert_rejected(self.accepted_files(), registry=False)
        with self.subTest("evals on base"):
            self.write(self.repo / f"evals/{SUBJECT}/old/fixture.yaml", "x\n")
            git(self.repo, "add", "-A")
            git(self.repo, "commit", "-q", "-m", "taken")
            self.assert_rejected(self.accepted_files())

    def test_a_bad_branch_name_or_moved_sha_is_rejected(self):
        self.branch(self.hook_files(), name="claude/draft-skill-oops")
        code, _, _ = self.validate(branch="claude/draft-skill-oops")
        self.assertEqual(code, 1)
        self.branch(self.hook_files())
        code, _, _ = run_main("validate", "--repo", str(self.repo), "--base", "main",
                              "--source", BRANCH, "--branch", BRANCH,
                              "--expect-sha", "3" * 40, "--out", str(self.tmp / "o"))
        self.assertEqual(code, 1)


# ---------------------------------------------------------------------------
# apply / fixtures-apply / bodies.

def sibling_registry() -> Path | None:
    entry = run_eval.resolve_registries(
        None, os.environ.get("SKILLS_EVALS_REGISTRIES"), REPO_ROOT,
        os.environ.get("AGENTSKILLS_DIR"))["acsi-agentskills"]
    path = entry["path"]
    return path if (path / "scripts" / "generate_readme_table.py").is_file() \
        and (path / ".git").exists() else None


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Staged(Case):
    def stage(self, record=None, decision="accepted") -> Path:
        staged = self.tmp / "staged"
        if decision == "hook-first":
            record = record or hook_record()
            self.write(staged / "summary.json", record)
            self.write(staged / "report.md", dns.hook_report(SUBJECT, 3, record["check_sketch"]))
            return staged
        record = record or skill_record()
        self.write(staged / "summary.json", record)
        self.write(staged / "report.md", dns.skill_report(record))
        self.write(staged / "skill.patch", dns.new_files_patch(
            dns.skill_prefix(PLUGIN, SUBJECT), skill_files()))
        for name, files in fixture_files().items():
            for rel, data in files.items():
                self.write(staged / f"evals/{SUBJECT}/{name}/{rel}", data)
        return staged


class Apply(Staged):
    def setUp(self):
        super().setUp()
        source = sibling_registry()
        if source is None:
            reason = "no acsi-agentskills checkout beside this repo; skipping apply"
            print(reason)
            self.skipTest(reason)
        self.tree = self.tmp / "registry"
        self.tree.mkdir()
        # Raw git is fine here (#343): reads the operator's registry checkout.
        archive = subprocess.run(["git", "-C", str(source), "archive", "--format=tar",
                                  "HEAD"], check=True, capture_output=True).stdout
        subprocess.run(["tar", "-x", "-C", str(self.tree)], input=archive, check=True)
        git(self.tree, "init", "-q", "-b", "main")
        git(self.tree, "add", "-A")
        git(self.tree, "commit", "-q", "-m", "registry")
        self.base = git(self.tree, "rev-parse", "HEAD")

    def test_apply_makes_every_addition_the_registry_checks_for(self):
        before = json.loads((self.tree / f"plugins/{PLUGIN}/plugin.json").read_text())
        code, out, err = run_main("apply", "--staged", str(self.stage()),
                                  "--tree", str(self.tree))
        self.assertEqual(code, 0, err)
        result = outputs(out)
        major, minor, patch = before["version"].split(".")
        bumped = f"{major}.{minor}.{int(patch) + 1}"
        self.assertEqual(result["version"], bumped)
        self.assertEqual(result["relock"], "true")
        for manifest in ("plugin.json", ".claude-plugin/plugin.json"):
            self.assertEqual(json.loads((self.tree / f"plugins/{PLUGIN}/{manifest}")
                                        .read_text())["version"], bumped)
        skill = self.tree / dns.skill_prefix(PLUGIN, SUBJECT)
        self.assertEqual((skill / "SKILL.md").read_text(), SKILL_MD)
        impact = (self.tree / "docs/skill-impact.md").read_text()
        entry = impact.index(f"## 2026-10-26 — {PLUGIN}/{SUBJECT} — create")
        self.assertLess(entry, impact.index("\n## 2026-10-23"))
        self.assertGreater(entry, impact.index("\n---\n", impact.index("## Entry format")))
        # The registry's own checks, loaded from the tree: the README table
        # is current and the bundle version moved.
        readme = load_module(self.tree / "scripts/generate_readme_table.py", "t_readme")
        self.assertEqual(readme.render(True), 0)
        self.assertIn(f"/{PLUGIN}:{SUBJECT}", (self.tree / "README.md").read_text())
        # check_plugin_versions.py imports jsonschema at load time, which
        # acsi-agentskills' CI installs and this repo's does not; the bump
        # itself is asserted above either way.
        if importlib.util.find_spec("jsonschema") is not None:
            versions = load_module(self.tree / "scripts/check_plugin_versions.py", "t_versions")
            problems, notices = [], []
            versions.check_bundle(self.tree, self.base, self.tree / "plugins" / PLUGIN,
                                  problems, notices)
            self.assertEqual(problems, [])

    def test_apply_refuses_a_taken_skill_or_a_non_accepted_record(self):
        staged = self.stage()
        (self.tree / dns.skill_prefix(PLUGIN, SUBJECT)).mkdir()
        code, _, _ = run_main("apply", "--staged", str(staged), "--tree", str(self.tree))
        self.assertEqual(code, 1)
        shutil.rmtree(self.tmp / "staged")
        code, _, _ = run_main("apply", "--staged", str(self.stage(decision="hook-first")),
                              "--tree", str(self.tree))
        self.assertEqual(code, 1)


class FixturesApply(Staged):
    def test_fixtures_land_with_their_readme_layout_entry(self):
        tree = self.tmp / "skills-evals"
        self.write(tree / "README.md", (REPO_ROOT / "README.md").read_text())
        code, out, err = run_main("fixtures-apply", "--staged", str(self.stage()),
                                  "--tree", str(tree))
        self.assertEqual(code, 0, err)
        self.assertEqual(outputs(out)["subject"], SUBJECT)
        for name in FIXTURE_NAMES:
            self.assertTrue((tree / f"evals/{SUBJECT}/{name}/fixture.yaml").is_file())
        # The suite's own rule (test_every_eval_directory_is_named_in_the_readmes_layout).
        text = (tree / "README.md").read_text()
        block = [b for b in text.split("```") if "evals/" in b and "harness/" in b][0]
        self.assertIn(SUBJECT + "/", block)
        self.assertLess(block.index(SUBJECT + "/"), block.index("  real-work/"))
        code, _, _ = run_main("fixtures-apply", "--staged", str(self.tmp / "staged"),
                              "--tree", str(tree))
        self.assertEqual(code, 1, "a second apply must refuse the existing directory")


class Bodies(Staged):
    def body(self, command: str, *extra: str, staged=None) -> tuple[int, str]:
        code, out, _ = run_main(command, "--staged", str(staged or self.stage()),
                                "--run-id", RUN_ID, "--sha", SHA, "--branch", BRANCH,
                                *extra)
        return code, out

    def test_both_pr_bodies_end_with_the_marker_and_close_nothing(self):
        for repo in ("acsi-agentskills", "skills-evals"):
            with self.subTest(repo):
                code, body = self.body("pr-body", "--repo", repo)
                self.assertEqual(code, 0)
                self.assertEqual(body.rstrip("\n").splitlines()[-1], dns.MARKER)
                self.assertIsNone(dns.text_problem(body.replace(dns.MARKER, "")))
                self.assertIn(f"# New skill: {SUBJECT}", body)
        _, skill_body = self.body("pr-body", "--repo", "acsi-agentskills")
        self.assertIn("- [ ] Examples, fixtures and docs use invented data only", skill_body)
        _, fixtures_body = self.body("pr-body", "--repo", "skills-evals")
        self.assertIn(f"draft-skill/{SUBJECT}", fixtures_body)

    def test_the_merge_mode_decides_what_both_bodies_promise(self):
        """improve_gate's one switch: in auto both pull requests merge
        themselves, the skill's first; draft (the default) keeps a person."""
        for repo in ("acsi-agentskills", "skills-evals"):
            with self.subTest(repo):
                code, auto = self.body("pr-body", "--repo", repo, "--mode", "auto")
                self.assertEqual(code, 0)
                _, draft = self.body("pr-body", "--repo", repo)
                self.assertEqual(draft, self.body("pr-body", "--repo", repo,
                                                  "--mode", "draft")[1])
                self.assertEqual(auto.rstrip("\n").splitlines()[-1], dns.MARKER)
                self.assertIsNone(dns.text_problem(auto.replace(dns.MARKER, "")))
                self.assertNotIn("nothing merges", auto)
                self.assertIn("merge this one first" if repo == "acsi-agentskills"
                              else "Merge this after", auto)
        _, skill = self.body("pr-body", "--repo", "acsi-agentskills", "--mode", "auto")
        self.assertIn("merges itself (merge commit)", skill)
        self.assertIn(dns.improve_gate.REGISTRY_ADR, skill)
        _, fixtures = self.body("pr-body", "--repo", "skills-evals", "--mode", "auto")
        self.assertIn("Auto-merge (merge commit) is enabled", fixtures)
        _, draft = self.body("pr-body", "--repo", "acsi-agentskills")
        self.assertIn("nothing merges itself", draft)
        with self.assertRaises(SystemExit):
            self.body("pr-body", "--repo", "skills-evals", "--mode", "yes")

    def test_the_issue_body_carries_one_exact_json_block(self):
        record = hook_record(proposal={**hook_record()["proposal"],
                                       "examples": ["ping @someone about #12"]})
        code, body = self.body("issue-body",
                               staged=self.stage(record, decision="hook-first"))
        self.assertEqual(code, 0)
        lines = body.rstrip("\n").splitlines()
        self.assertEqual(lines[-1], dns.MARKER)
        self.assertEqual(body.count("```json\n"), 1)
        self.assertEqual(body.count("```"), 2)
        block = body.split("```json\n", 1)[1].split("\n```", 1)[0]
        doc = json.loads(block)
        self.assertEqual(list(doc), ["schema_version", "id", "subject", "kind",
                                     "evidence_count", "examples", "check_sketch"])
        self.assertEqual(doc["kind"], "agent-check")
        self.assertEqual(doc["examples"], ["ping @someone about #12"])
        self.assertEqual(set(doc["check_sketch"]), set(dns.CHECK_SKETCH_LIMITS))
        self.assertTrue(body.startswith("A hook proposal"))
        self.assertIn(f"# Hook first: {SUBJECT}", body)
        # Escaped in the body, so GitHub notifies no one and links nothing.
        self.assertNotIn("@someone", body)
        self.assertNotIn("#12", body)

    def test_bodies_take_only_validated_values(self):
        for command, extra in (("pr-body", ("--repo", "skills-evals")), ("issue-body", ())):
            with self.subTest(command):
                code, out, _ = run_main(command, "--staged", str(self.tmp / "x"),
                                        "--run-id", RUN_ID, "--sha", "bad",
                                        "--branch", BRANCH, *extra)
                self.assertEqual((code, out), (1, ""))
        code, _ = self.body("issue-body")
        self.assertEqual(code, 1, "a hook issue needs a hook-first record")


if __name__ == "__main__":
    unittest.main()
