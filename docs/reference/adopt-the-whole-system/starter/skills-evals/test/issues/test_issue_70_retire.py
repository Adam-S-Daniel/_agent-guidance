#!/usr/bin/env python3
"""Issue #70, part 2: scripts/retire_skill.py acts on a `retire` verdict.

The owner's decisions: build automatic removal (2026-10-25); removal pull requests
auto-merge with no human review (2026-10-25, ADR 0014); results are not
signed, the gate guards against honest mistakes (2026-10-26); removal pull
requests auto-merge with no human (2026-10-26, acsi-agentskills ADR 0021),
so the committed `retirement_mode` is `auto`, lowered to `proposal` (an
issue) when the skill's plugin runs code or the workflow has no write token.

Hermetic: a hand-written gates document, registry checkouts in a temporary
directory (local git only), and a fake `gh` and `push`; no network, no clock.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "test"))

from lockfile_pins import lock_problem  # noqa: E402
import gates as gates_module  # noqa: E402
import compute_gates  # noqa: E402
import retire_skill  # noqa: E402

SCHEDULE = {"GITHUB_EVENT_NAME": "schedule"}
AUTO_ENV = {**SCHEDULE, "GITHUB_ACTIONS": "true", "GITHUB_REF": "refs/heads/main"}
BOT = "github-actions[bot]"
MODELS = ["claude-opus-5-5", "claude-sonnet-5-5"]
SKILL = "example-skill"
PLUGIN = "acsi-coding-anywhere"
IMPACT = """# skill-impact.md — the registry's skill-change audit trail

Header text.

---


## 2026-10-23 — acsi-coding-anywhere/other-skill — edit

- Motivation: an earlier entry.
"""


def cell(fixture, model, cls="no-benefit"):
    return {"fixture": fixture, "model": model, "class": cls,
            "with": {"runs": 10}, "without": {"runs": 11},
            "objective": {"delta": 0.0, "benefit_lower": -0.1, "retire_upper": 0.04},
            "judge": {"delta": 0.1, "benefit_lower": -0.2, "retire_upper": 0.3},
            "looks": [{"n": 5, "class": cls}, {"n": 10, "class": cls}]}


def gates_doc(mode="proposal", verdicts=None):
    verdicts = verdicts if verdicts is not None else {
        SKILL: {"verdict": "retire", "reason": "every cell shows no benefit",
                "cells": [cell("basic", m) for m in MODELS] +
                         [cell("edge", m) for m in MODELS]},
        "kept-skill": {"verdict": "keep", "reason": "a cell shows benefit", "cells": []}}
    return {"schema_version": 1, "as_of": "20261025T120000Z", "roster_models": MODELS,
            "retirement_mode": mode, "skills": verdicts}


def issue(number, body, author=BOT, state="open"):
    """One record as open_issue's `--jq` filter shapes it."""
    return {"number": number, "body": body, "author": author, "state": state}


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t",
                           "-c", "user.email=t@example.com", *args],
                          check=True, capture_output=True, text=True).stdout


KEPT = "other-skill"
LOCKED = ("acsi-anything-anywhere", PLUGIN)

# Stand-ins for acsi-agentskills' own generators, which retire_skill.py runs in
# the checkout. Each appends one JSON line to `<checkout>.calls` (outside the
# checkout, so it never dirties it) and exits 3 while `<checkout>.fail-<name>`
# exists. The README one rebuilds the table from the plugin directories; the
# lock one pins HEAD and digests each locked bundle's skills, as
# generate_skills_lock.py --repin does, and --check-current compares the
# working tree with the pinned commit.
FAKE_COMMON = '''import hashlib, json, os, subprocess, sys
from pathlib import Path
ROOT = Path.cwd()
NAME = Path(sys.argv[0]).stem
with open(f"{ROOT}.calls", "a") as log:
    log.write(json.dumps({"script": NAME, "args": sys.argv[1:],
                          "env": sorted(os.environ)}) + "\\n")
if Path(f"{ROOT}.fail-{NAME}").exists():
    sys.exit(3)
'''
FAKE_README = FAKE_COMMON + '''text = (ROOT / "README.md").read_text()
head, rest = text.split("<!-- BEGIN -->\\n", 1)
_, tail = rest.split("<!-- END -->\\n", 1)
rows = [f"| `{p.parent.parent.name}` | `/{p.parent.parent.name}:{p.name}` |\\n"
        for p in sorted(ROOT.glob("plugins/*/skills/*")) if p.is_dir()]
(ROOT / "README.md").write_text(head + "<!-- BEGIN -->\\n" + "".join(rows)
                                + "<!-- END -->\\n" + tail)
'''
FAKE_LOCK = FAKE_COMMON + '''def digests(tree, bundles):
    return {f"{b}/{p.name}": "sha256:" + hashlib.sha256(
                (p / "SKILL.md").read_bytes()).hexdigest()
            for b in bundles for p in sorted((tree / "plugins" / b / "skills").glob("*"))}
lock = json.loads((ROOT / "skills.lock").read_text())
if sys.argv[1:] == ["--repin"]:
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                          check=True).stdout.strip()
    lock.update(ref=head, skills=digests(ROOT, lock["bundles"]), generated_from=head)
    (ROOT / "skills.lock").write_text(json.dumps(lock, indent=2) + "\\n")
elif sys.argv[1:] == ["--check-current"]:
    import tarfile, io, tempfile
    tmp = Path(tempfile.mkdtemp())
    data = subprocess.run(["git", "archive", lock["ref"]], capture_output=True,
                          check=True).stdout
    tarfile.open(fileobj=io.BytesIO(data)).extractall(tmp)
    sys.exit(0 if digests(tmp, lock["bundles"]) == digests(ROOT, lock["bundles"]) else 1)
else:
    sys.exit(2)
'''


def calls(registry: Path) -> list:
    """The fake generators' invocations, oldest first."""
    path = Path(f"{registry}.calls")
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def make_registry(root: Path, skills=((PLUGIN, SKILL), (PLUGIN, KEPT)),
                  version="1.2.7", lock_bundles=LOCKED) -> Path:
    root.mkdir(parents=True)
    plugins = {p for p, _ in skills} | {PLUGIN}
    for plugin in plugins:
        (root / "plugins" / plugin / ".claude-plugin").mkdir(parents=True)
        (root / "plugins" / plugin / "plugin.json").write_text(
            json.dumps({"name": plugin, "version": version}, indent=2) + "\n")
        (root / "plugins" / plugin / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": plugin, "version": version}, indent=2) + "\n")
    for plugin, skill in skills:
        directory = root / "plugins" / plugin / "skills" / skill
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(f"---\nname: {skill}\n---\nBody.\n")
    (root / "docs").mkdir()
    (root / "docs" / "skill-impact.md").write_text(IMPACT)
    (root / "scripts").mkdir()
    layout = {"plugins": {p: {"defaultEnabled": True,
                              "skills": sorted(s for q, s in skills if q == p)}
                          for p in sorted(plugins)}}
    (root / "scripts" / "plugin_layout.json").write_text(json.dumps(layout, indent=2) + "\n")
    (root / "scripts" / "generate_readme_table.py").write_text(FAKE_README)
    (root / "scripts" / "generate_skills_lock.py").write_text(FAKE_LOCK)
    (root / "README.md").write_text("# Registry\n\n<!-- BEGIN -->\n<!-- END -->\n\nMore.\n")
    subprocess.run([sys.executable, "scripts/generate_readme_table.py"], cwd=root, check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    if lock_bundles is not None:
        (root / "skills.lock").write_text(json.dumps(
            {"registry": "example-adopter/acsi-agentskills", "ref": "", "bundles":
             list(lock_bundles), "skills": {}, "generated_from": ""}, indent=2) + "\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "seed")
    if lock_bundles is not None:
        subprocess.run([sys.executable, "scripts/generate_skills_lock.py", "--repin"],
                       cwd=root, check=True)
        git(root, "commit", "-qam", "pin")
    Path(f"{root}.calls").unlink(missing_ok=True)
    return root


class FakeGitHub:
    """Records every call; answers the reads from canned data: open pull
    requests, closed ones (the `state=closed` search), issues, and the
    auto-merge read-back."""

    def __init__(self, prs=(), issues=(), closed=(), merge_method="MERGE"):
        self.prs, self.issues, self.calls, self.pushes = list(prs), list(issues), [], []
        self.closed, self.merge_method = list(closed), merge_method

    def gh(self, args, stdin=None):
        self.calls.append((list(args), stdin))
        if args[:2] == ["api", "--paginate"] and "/pulls?" in args[2]:
            prs = self.closed if "state=closed" in args[2] else self.prs
            return "".join(json.dumps(p) + "\n" for p in prs)
        if args[:2] == ["pr", "view"]:
            return self.merge_method + "\n"
        if args[:2] == ["api", "--paginate"] and "/issues?" in args[2]:
            return "".join(json.dumps(i) + "\n" for i in self.issues)
        if args[:2] == ["pr", "create"]:
            return "https://github.com/example-adopter/acsi-agentskills/pull/999\n"
        return ""

    def push(self, checkout, branch):
        self.pushes.append((Path(checkout), branch))

    def writes(self):
        return [args for args, _ in self.calls if args[0] != "api"]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(self.tmp)], check=False))
        self.registry = make_registry(self.tmp / "agentskills")

    def write_gates(self, doc):
        path = self.tmp / "gates.json"
        path.write_text(json.dumps(doc))
        return path

    def run_main(self, doc, *extra, env=SCHEDULE, fake=None, registries=None):
        fake = fake or FakeGitHub()
        argv = ["--gates", str(self.write_gates(doc))]
        for flag in registries or [f"acsi-agentskills={self.registry}"]:
            argv += ["--registry", flag]
        out, err = io.StringIO(), io.StringIO()
        with redirect_stderr(err):
            code = retire_skill.main([*argv, *extra], env=env, gh=fake.gh,
                                     push=fake.push, out=out)
        return code, out.getvalue(), err.getvalue(), fake

    def assertUntouched(self):
        self.assertEqual(git(self.registry, "status", "--porcelain"), "")
        self.assertEqual(git(self.registry, "branch", "--show-current").strip(), "main")
        self.assertTrue((self.registry / "plugins" / PLUGIN / "skills" / SKILL).is_dir())


class EventTests(Base):
    def test_refuses_outside_the_schedule(self):
        for env in ({}, {"GITHUB_EVENT_NAME": "workflow_dispatch"},
                    {"GITHUB_EVENT_NAME": "pull_request"},
                    {"GITHUB_EVENT_NAME": "pull_request_target"},
                    {"GITHUB_EVENT_NAME": "Schedule"}):
            for mode in ("proposal", "auto"):
                with self.subTest(env=env, mode=mode):
                    code, out, err, fake = self.run_main(gates_doc(mode), env=env)
                    self.assertEqual(code, 2)
                    self.assertIn("only the scheduled workflow", err)
                    self.assertEqual(fake.calls, [])
                    self.assertEqual(fake.pushes, [])
                    self.assertUntouched()

    def test_check_event_accepts_only_schedule(self):
        retire_skill.check_event({"GITHUB_EVENT_NAME": "schedule"})
        with self.assertRaises(retire_skill.RetireError):
            retire_skill.check_event({"GITHUB_EVENT_NAME": "workflow_dispatch"})


class ProposalTests(Base):
    def test_files_an_issue_when_none_is_open(self):
        code, out, err, fake = self.run_main(gates_doc("proposal"))
        self.assertEqual(code, 0, err)
        writes = fake.writes()
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][:4], ["issue", "create", "-R", "example-adopter/skills-evals"])
        body = fake.calls[-1][1]
        self.assertIn(retire_skill.ISSUE_MARKER.format(skill=SKILL), body)
        self.assertIn(f"plugins/{PLUGIN}/skills/{SKILL}", body)
        self.assertIn("| basic | claude-opus-5-5 | 10 | 11 | 0.040 | 0.300 | 5, 10 |", body)
        self.assertEqual(fake.pushes, [])
        self.assertFalse(any(args[0] == "pr" for args in writes))
        self.assertUntouched()

    def test_updates_the_open_issue_found_by_its_marker(self):
        marker = retire_skill.ISSUE_MARKER.format(skill=SKILL)
        fake = FakeGitHub(issues=[issue(5, "unrelated"),
                                  issue(42, f"{marker}\nold text"),
                                  issue(7, retire_skill.ISSUE_MARKER.format(
                                      skill="other-skill"))])
        code, out, err, fake = self.run_main(gates_doc("proposal"), fake=fake)
        self.assertEqual(code, 0, err)
        self.assertEqual(fake.writes(), [["issue", "edit", "42", "-R",
                                          "example-adopter/skills-evals", "--body-file", "-"]])
        self.assertIn("updated issue #42", out)

    def test_an_open_removal_pr_does_not_stop_a_proposal(self):
        fake = FakeGitHub(prs=[{"number": 1, "head": "retire-skill/x", "url": "u"}])
        code, _, err, fake = self.run_main(gates_doc("proposal"), fake=fake)
        self.assertEqual(code, 0, err)
        self.assertEqual(fake.writes()[0][:2], ["issue", "create"])

    def test_a_skill_not_found_once_is_still_proposed_and_says_so(self):
        registry = make_registry(self.tmp / "two", skills=((PLUGIN, SKILL),
                                                           ("acsi-coding-local", SKILL)))
        code, _, err, fake = self.run_main(
            gates_doc("proposal"), registries=[f"acsi-agentskills={registry}"])
        self.assertEqual(code, 0, err)
        self.assertIn("not located", fake.calls[-1][1])
        self.assertIn("found 2 time(s)", fake.calls[-1][1])

    def test_a_missing_or_invalid_mode_is_proposal(self):
        for mode in (None, "AUTO", "", 1):
            doc = gates_doc("proposal")
            doc["retirement_mode"] = mode
            self.assertEqual(retire_skill.mode_of(doc), "proposal")
        self.assertEqual(gates_module.parse_gate_policy(
            "skills:\n" + "".join(f"  {k}: {json.dumps(v)}\n"
                                  for k, v in self.policy_block().items()))["skills"]
            ["retirement_mode"], "proposal")

    @staticmethod
    def policy_block():
        doc = yaml.safe_load((ROOT / "evals" / "gate-policy.yml").read_text())
        block = dict(doc["skills"])
        block["retirement_mode"] = "sometimes"
        return block

    def test_the_committed_policy_is_auto(self):
        """the owner, 2026-10-26: removal pull requests auto-merge, no human."""
        doc = yaml.safe_load((ROOT / "evals" / "gate-policy.yml").read_text())
        self.assertEqual(doc["skills"]["retirement_mode"], "auto")
        self.assertEqual(doc["guidance"]["retirement_mode"], "auto")


class AutoTests(Base):
    def run_main(self, doc, *extra, env=AUTO_ENV, **kwargs):
        if "--allow-mode" not in extra:
            extra = (*extra, "--allow-mode", "auto")
        return super().run_main(doc, *extra, env=env, **kwargs)

    def test_prepares_pushes_opens_and_arms_the_removal(self):
        code, out, err, fake = self.run_main(gates_doc("auto"))
        self.assertEqual(code, 0, err)
        branch = f"retire-skill/{SKILL}"
        self.assertEqual(git(self.registry, "branch", "--show-current").strip(), branch)
        self.assertEqual(git(self.registry, "status", "--porcelain"), "")
        self.assertFalse((self.registry / "plugins" / PLUGIN / "skills" / SKILL).exists())
        changed = sorted(git(self.registry, "diff", "--name-only", "main", "HEAD").split())
        self.assertEqual(changed, sorted([
            "docs/skill-impact.md", f"plugins/{PLUGIN}/.claude-plugin/plugin.json",
            f"plugins/{PLUGIN}/plugin.json", f"plugins/{PLUGIN}/skills/{SKILL}/SKILL.md",
            "scripts/plugin_layout.json", "README.md", "skills.lock"]))
        for rel in (f"plugins/{PLUGIN}/plugin.json",
                    f"plugins/{PLUGIN}/.claude-plugin/plugin.json"):
            self.assertEqual(json.loads((self.registry / rel).read_text())["version"], "2.0.0")
        self.assertEqual(fake.pushes, [(self.registry.resolve(), branch)])
        url = "https://github.com/example-adopter/acsi-agentskills/pull/999"
        head = git(self.registry, "rev-parse", "HEAD").strip()
        writes = [args for args, _ in fake.calls if args[:2] != ["api", "--paginate"]]
        self.assertEqual(writes[0][:6], ["pr", "create", "-R",
                                         "example-adopter/acsi-agentskills", "--base", "main"])
        self.assertIn(branch, writes[0])
        self.assertNotIn("--draft", writes[0])
        self.assertEqual(writes[1], ["api", "-X", "POST",
                                     "repos/example-adopter/acsi-agentskills/issues/999/labels",
                                     "-f", "labels[]=agent-authored"])
        self.assertEqual(writes[2], ["pr", "merge", url, "-R", "example-adopter/acsi-agentskills",
                                     "--auto", "--merge", "--match-head-commit", head])
        self.assertEqual(writes[3][:3], ["pr", "view", url])
        self.assertEqual(len(writes), 4)
        body = next(stdin for args, stdin in fake.calls if args[:2] == ["pr", "create"])
        self.assertIn("no human review", body)
        self.assertIn("0014-removal-pull-requests-merge-without-review.md", body)
        self.assertIn("0021-automated-skill-changes-merge-without-review.md", body)
        self.assertIn("1.2.7 -> 2.0.0", body)
        self.assertTrue(body.endswith(retire_skill.AGENT_MARKER + "\n"))
        self.assertFalse(any(args[0] == "issue" for args in writes))
        for message in git(self.registry, "log", "--format=%B%x00", "main..HEAD").split("\0"):
            if message.strip():
                self.assertTrue(message.strip().endswith("Agent: claude-code"), message)

    def test_any_open_loop_pr_holds_the_slot_and_nothing_is_opened(self):
        for head in ("retire-skill/other-skill", "eval-improve/other-skill",
                     "draft-skill/new-skill"):
            with self.subTest(head=head):
                fake = FakeGitHub(prs=[{"number": 3, "head": head, "url":
                                        "https://github.com/example-adopter/acsi-agentskills/pull/3"}])
                code, out, err, fake = self.run_main(gates_doc("auto"), fake=fake)
                self.assertEqual(code, 0, err)
                self.assertIn("already open", out)
                self.assertIn("waits", out)
                self.assertIn("/pull/3", out)
                self.assertEqual(fake.writes(), [])
                self.assertEqual(fake.pushes, [])
                self.assertUntouched()

    def test_a_closed_removal_pr_means_the_skill_is_decided(self):
        fake = FakeGitHub(closed=[{"number": 8, "head": f"retire-skill/{SKILL}", "url":
                                   "https://github.com/example-adopter/acsi-agentskills/pull/8"}])
        code, out, err, fake = self.run_main(gates_doc("auto"), fake=fake)
        self.assertEqual(code, 0, err)
        self.assertIn("already decided", out)
        self.assertIn("/pull/8", out)
        self.assertEqual(fake.writes(), [])
        self.assertEqual(fake.pushes, [])
        self.assertUntouched()
        search = [args[2] for args, _ in fake.calls if "state=closed" in args[2]]
        self.assertEqual(search, ["repos/example-adopter/acsi-agentskills/pulls?state=closed"
                                  f"&per_page=100&head=example-adopter:retire-skill/{SKILL}"])

    def test_a_closed_pr_from_another_head_does_not_decide(self):
        fake = FakeGitHub(closed=[{"number": 8, "head": "retire-skill/other-skill", "url": "u"}])
        code, _, err, fake = self.run_main(gates_doc("auto"), fake=fake)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(fake.pushes), 1)

    def test_a_plugin_that_runs_code_falls_back_to_the_issue(self):
        cases = {"hooks dir": lambda r: (r / "plugins" / PLUGIN / "hooks").mkdir(),
                 "bin dir": lambda r: (r / "plugins" / PLUGIN / "bin").mkdir(),
                 "package file": lambda r: (r / "plugins" / PLUGIN / "package.json")
                 .write_text("{}\n"),
                 "manifest hooks key": lambda r: (r / "plugins" / PLUGIN / "plugin.json")
                 .write_text(json.dumps({"name": PLUGIN, "version": "1.2.7",
                                         "hooks": "./hooks/hooks.json"}, indent=2) + "\n")}
        for name, mutate in cases.items():
            with self.subTest(name):
                registry = make_registry(self.tmp / name.replace(" ", "-"))
                mutate(registry)
                git(registry, "add", "-A")
                git(registry, "commit", "-q", "--allow-empty", "-m", "runtime")
                code, _, err, fake = self.run_main(
                    gates_doc("auto"), *BOTH, registries=[f"acsi-agentskills={registry}"])
                self.assertEqual(code, 0, err)
                self.assertIn("lowered to proposal", err)
                writes = fake.writes()
                self.assertEqual(len(writes), 1)
                self.assertEqual(writes[0][:2], ["issue", "create"])
                body = fake.calls[-1][1]
                self.assertIn("`retirement_mode: auto`, but the skill's plugin runs code", body)
                self.assertEqual(fake.pushes, [])
                self.assertEqual(git(registry, "branch", "--show-current").strip(), "main")

    def test_a_plugin_without_runtime_paths_is_not_lowered(self):
        registries = compute_gates.flag_registries([f"acsi-agentskills={self.registry}"])
        action = retire_skill.plan(gates_doc("auto"), registries, FakeGitHub().gh,
                                   allowed=("auto", "proposal"))
        self.assertEqual(action["action"], "removal")
        self.assertNotIn("mode_reason", action)

    def test_a_runtime_plugin_with_only_auto_allowed_is_refused(self):
        (self.registry / "plugins" / PLUGIN / "bin").mkdir()
        (self.registry / "plugins" / PLUGIN / "bin" / "tool").write_text("x\n")
        git(self.registry, "add", "-A")
        git(self.registry, "commit", "-qm", "bin")
        code, _, err, fake = self.run_main(gates_doc("auto"))
        self.assertEqual(code, 2)
        self.assertIn("falls back to proposal", err)
        self.assertEqual(fake.writes(), [])

    def test_no_credential_falls_back_to_the_issue(self):
        code, _, err, fake = self.run_main(gates_doc("auto"), "--allow-mode", "proposal",
                                           "--fallback-to-proposal")
        self.assertEqual(code, 0, err)
        self.assertIn("lowered to proposal", err)
        self.assertEqual([w[:2] for w in fake.writes()], [["issue", "create"]])
        self.assertIn("no write credential for acsi-agentskills", fake.calls[-1][1])
        self.assertEqual(fake.pushes, [])
        self.assertUntouched()

    def test_the_fallback_does_not_touch_a_proposal_or_an_allowed_auto(self):
        code, _, err, fake = self.run_main(gates_doc("proposal"), "--allow-mode", "proposal",
                                           "--fallback-to-proposal")
        self.assertEqual(code, 0, err)
        self.assertNotIn("lowered", err)
        self.assertIn("sets `retirement_mode: proposal`", fake.calls[-1][1])
        code, _, err, fake = self.run_main(gates_doc("auto"), *BOTH, "--fallback-to-proposal")
        self.assertEqual(code, 0, err)
        self.assertEqual(len(fake.pushes), 1)

    def test_a_label_failure_stops_before_auto_merge(self):
        fake = FakeGitHub()
        real = fake.gh

        def gh(args, stdin=None):
            if args[:3] == ["api", "-X", "POST"]:
                fake.calls.append((list(args), stdin))
                raise retire_skill.RetireError("gh api exited 1")
            return real(args, stdin)
        fake.gh = gh
        code, _, err, fake = self.run_main(gates_doc("auto"), fake=fake)
        self.assertEqual(code, 2)
        self.assertIn("gh api exited 1", err)
        self.assertFalse(any(args[:2] == ["pr", "merge"] for args, _ in fake.calls))

    def test_auto_merge_that_did_not_take_is_refused(self):
        code, _, err, fake = self.run_main(gates_doc("auto"),
                                           fake=FakeGitHub(merge_method="SQUASH"))
        self.assertEqual(code, 2)
        self.assertIn("auto-merge (merge commit) is not enabled", err)

    def test_a_create_that_prints_no_pr_url_stops_there(self):
        fake = FakeGitHub()
        real = fake.gh

        def gh(args, stdin=None):
            out = real(args, stdin)
            return "https://github.com/someone/else/pull/1\n" if args[:2] == ["pr", "create"] \
                else out
        fake.gh = gh
        code, _, err, fake = self.run_main(gates_doc("auto"), fake=fake)
        self.assertEqual(code, 2)
        self.assertIn("no pull request URL", err)
        self.assertFalse(any(args[:2] == ["pr", "merge"] or args[:3] == ["api", "-X", "POST"]
                             for args, _ in fake.calls))

    def test_registry_writes_go_through_registry_gh(self):
        reads, writes = FakeGitHub(), FakeGitHub()
        argv = ["--gates", str(self.write_gates(gates_doc("auto"))),
                "--registry", f"acsi-agentskills={self.registry}", "--allow-mode", "auto"]
        code = retire_skill.main(argv, env=AUTO_ENV, gh=reads.gh, push=reads.push,
                                 registry_gh=writes.gh, out=io.StringIO())
        self.assertEqual(code, 0)
        self.assertEqual(reads.writes(), [])
        self.assertTrue(all(args[:2] == ["api", "--paginate"] for args, _ in reads.calls))
        self.assertEqual([args[:2] for args, _ in writes.calls],
                         [["pr", "create"], ["api", "-X"], ["pr", "merge"], ["pr", "view"]])

    def test_the_date_flag_dates_the_impact_entry(self):
        code, _, err, _ = self.run_main(gates_doc("auto"), "--date", "2026-10-27")
        self.assertEqual(code, 0, err)
        text = (self.registry / "docs" / "skill-impact.md").read_text()
        self.assertIn(f"\n---\n\n## 2026-10-27 — {PLUGIN}/{SKILL} — remove\n", text)
        for bad in ("2026-1-27", "20261027", "2026-10-27\n"):
            with self.subTest(bad=bad):
                code, _, err, _ = self.run_main(gates_doc("auto"), "--date", bad)
                self.assertEqual(code, 2)
                self.assertIn("--date", err)

    def test_other_open_prs_do_not_hold_the_slot(self):
        fake = FakeGitHub(prs=[{"number": 4, "head": "claude/retire-skill-docs", "url": "u"},
                               {"number": 5, "head": "main-retire-skill/x", "url": "u"},
                               {"number": 6, "head": "claude/eval-improve-x", "url": "u"},
                               {"number": 7, "head": "draft-skill", "url": "u"}])
        code, _, err, fake = self.run_main(gates_doc("auto"), fake=fake)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(fake.pushes), 1)

    def test_basename_must_be_found_exactly_once(self):
        cases = {
            "two plugins": [f"acsi-agentskills={make_registry(self.tmp / 'a', skills=((PLUGIN, SKILL), ('acsi-coding-local', SKILL)))}"],
            "absent": [f"acsi-agentskills={make_registry(self.tmp / 'b', skills=((PLUGIN, 'another-skill'),))}"],
            "another registry": [f"acsi-agentskills={self.registry}",
                                 f"acsi-agentskills-private={make_registry(self.tmp / 'c', skills=((PLUGIN, SKILL),))}"],
        }
        for name, flags in cases.items():
            with self.subTest(name):
                code, _, err, fake = self.run_main(gates_doc("auto"), registries=flags)
                self.assertEqual(code, 2)
                self.assertIn("not exactly once", err)
                self.assertEqual(fake.writes(), [])
                self.assertEqual(fake.pushes, [])
        self.assertUntouched()

    def test_a_directory_without_skill_md_still_counts_as_a_copy(self):
        registry = make_registry(self.tmp / "d")
        (registry / "plugins" / "acsi-coding-local" / "skills" / SKILL).mkdir(parents=True)
        code, _, err, _ = self.run_main(gates_doc("auto"),
                                        registries=[f"acsi-agentskills={registry}"])
        self.assertEqual(code, 2)
        self.assertIn("not exactly once", err)

    def test_no_acsi_agentskills_checkout_is_refused(self):
        code, _, err, fake = self.run_main(
            gates_doc("auto"), registries=[f"acsi-agentskills-private={self.make_flat(self.tmp / 'e')}"])
        self.assertEqual(code, 2)
        self.assertEqual(fake.pushes, [])

    def test_manifests_that_disagree_are_refused(self):
        path = self.registry / "plugins" / PLUGIN / "plugin.json"
        path.write_text(json.dumps({"name": PLUGIN, "version": "1.2.6"}))
        git(self.registry, "commit", "-qam", "drift")
        code, _, err, fake = self.run_main(gates_doc("auto"))
        self.assertEqual(code, 2)
        self.assertIn("disagree", err)
        self.assertEqual(fake.pushes, [])

    def test_a_dirty_checkout_is_refused(self):
        (self.registry / "stray.txt").write_text("x\n")
        code, _, err, fake = self.run_main(gates_doc("auto"))
        self.assertEqual(code, 2)
        self.assertIn("uncommitted changes", err)
        self.assertEqual(fake.pushes, [])
        self.assertEqual(git(self.registry, "branch", "--show-current").strip(), "main")

    def test_auto_is_refused_where_the_caller_allows_only_proposal(self):
        code, _, err, fake = self.run_main(gates_doc("auto"), "--allow-mode", "proposal")
        self.assertEqual(code, 2)
        self.assertIn("has not wired", err)
        self.assertEqual(fake.calls, [])
        self.assertUntouched()

    @staticmethod
    def make_flat(root: Path) -> Path:
        (root / "skills" / SKILL).mkdir(parents=True)
        (root / "skills" / SKILL / "SKILL.md").write_text("x\n")
        return root


class VerdictTests(Base):
    def test_no_retire_verdict_does_nothing(self):
        doc = gates_doc("auto", verdicts={SKILL: {"verdict": "retire-pending", "cells": []}})
        code, out, err, fake = self.run_main(doc)
        self.assertEqual(code, 0, err)
        self.assertIn("no skill has a retire verdict", out)
        self.assertEqual(fake.calls, [])
        self.assertUntouched()

    def test_a_retire_the_cells_do_not_support_is_refused(self):
        bad = {
            "a cell is not no-benefit": [cell("basic", MODELS[0]),
                                         cell("basic", MODELS[1], "no-benefit-pending")],
            "a roster model is missing": [cell("basic", MODELS[0])],
            "an extra model": [cell("basic", m) for m in [*MODELS, "claude-old"]],
            "no cell": [],
        }
        for name, cells in bad.items():
            with self.subTest(name):
                doc = gates_doc("proposal", verdicts={SKILL: {"verdict": "retire",
                                                              "cells": cells}})
                code, _, err, fake = self.run_main(doc)
                self.assertEqual(code, 2)
                self.assertEqual(fake.writes(), [])

    def test_two_retire_verdicts_are_refused(self):
        cells = [cell("basic", m) for m in MODELS]
        doc = gates_doc("auto", verdicts={SKILL: {"verdict": "retire", "cells": cells},
                                          "second-skill": {"verdict": "retire",
                                                           "cells": cells}})
        code, _, err, fake = self.run_main(doc)
        self.assertEqual(code, 2)
        self.assertIn("2 skills", err)
        self.assertEqual(fake.calls, [])

    def test_a_malformed_gates_file_is_refused(self):
        for doc in ({}, {**gates_doc(), "schema_version": 2},
                    {**gates_doc(), "as_of": "yesterday"}, []):
            with self.subTest(doc=doc):
                code, _, err, fake = self.run_main(doc)
                self.assertEqual(code, 2)
                self.assertEqual(fake.calls, [])

    def test_a_name_that_is_not_a_basename_is_refused(self):
        cells = [cell("basic", m) for m in MODELS]
        doc = gates_doc("proposal", verdicts={"../escape": {"verdict": "retire",
                                                            "cells": cells}})
        code, _, err, fake = self.run_main(doc)
        self.assertEqual(code, 2)
        self.assertEqual(fake.calls, [])


BOTH = ("--allow-mode", "auto", "--allow-mode", "proposal")


class DryRunTests(Base):
    def test_dry_run_writes_nothing_and_prints_the_plan(self):
        for mode, action in (("proposal", "create-issue"), ("auto", "removal")):
            for env in (SCHEDULE, {}):
                with self.subTest(mode=mode, env=env):
                    code, out, err, fake = self.run_main(gates_doc(mode), "--dry-run",
                                                         *BOTH, env=env)
                    self.assertEqual(code, 0, err)
                    plan = json.loads(out)
                    self.assertTrue(plan["dry_run"])
                    self.assertEqual(plan["action"], action)
                    self.assertEqual(fake.writes(), [])
                    self.assertEqual(fake.pushes, [])
                    self.assertTrue(plan["would_run"])
                    self.assertUntouched()

    def test_dry_run_auto_names_every_write(self):
        code, out, err, _ = self.run_main(gates_doc("auto"), "--dry-run", *BOTH)
        plan = json.loads(out)
        self.assertEqual(plan["path"], f"plugins/{PLUGIN}/skills/{SKILL}")
        self.assertEqual(plan["branch"], f"retire-skill/{SKILL}")
        heads = [command[:3] for command in plan["would_run"]]
        self.assertIn(["git", "push", "origin"], heads)
        self.assertIn(["gh", "pr", "create"], heads)
        self.assertIn(["gh", "pr", "merge"], heads)
        merge = next(c for c in plan["would_run"] if c[:3] == ["gh", "pr", "merge"])
        self.assertEqual(merge[-4:], ["--auto", "--merge", "--match-head-commit",
                                      "<removal head sha>"])
        self.assertIn(["gh", "api", "-X"], heads)
        self.assertIn(["git", "merge-base", "--is-ancestor"], heads)
        self.assertIn("## 2026-10-25 —", plan["impact_entry"])

    def test_dry_run_still_honors_the_one_open_pr_limit(self):
        fake = FakeGitHub(prs=[{"number": 3, "head": "eval-improve/x", "url": "u"}])
        code, out, err, _ = self.run_main(gates_doc("auto"), "--dry-run", *BOTH, fake=fake)
        self.assertEqual(code, 0, err)
        plan = json.loads(out)
        self.assertEqual((plan["action"], plan["would_run"]), ("none", []))
        self.assertIn("already open", plan["reason"])


class UsageLineTests(Base):
    """The issue names the skill's recent invocations from --usage, and the
    decision does not depend on it."""

    def usage(self, generated_at="2026-10-25T06:00:00Z", rows=None):
        rows = rows if rows is not None else [
            {"week": "2026-10-19", "kind": "skill", "name": SKILL, "repo": None,
             "surface": "local", "invocations": 3, "sessions": 2, "blocked": None,
             "overridden": None, "outcome": None},
            {"week": "2026-10-19", "kind": "hook", "name": SKILL, "repo": None,
             "surface": "local", "invocations": 50, "sessions": None, "blocked": 0,
             "overridden": None, "outcome": None}]
        path = self.tmp / "weekly-local.json"
        path.write_text(json.dumps({"generated_at": generated_at, "schema": 1,
                                    "sources": [], "rows": rows}))
        return str(path)

    def body(self, *extra):
        code, _, err, fake = self.run_main(gates_doc("proposal"), *extra)
        self.assertEqual(code, 0, err)
        self.assertEqual(fake.writes()[0][:2], ["issue", "create"])
        return fake.calls[-1][1]

    def test_a_fresh_census_gives_the_count(self):
        self.assertIn("Recent use: 3 local invocation(s) in the four weeks to "
                      "20261025T120000Z", self.body("--usage", self.usage()))

    def test_a_skill_absent_from_a_fresh_census_is_zero(self):
        self.assertIn("Recent use: 0 local invocation(s)",
                      self.body("--usage", self.usage(rows=[])))

    def test_no_stale_or_unreadable_census_is_unknown(self):
        for extra in ((), ("--usage", self.usage(generated_at="2026-10-01T00:00:00Z")),
                      ("--usage", str(self.tmp / "absent.json"))):
            with self.subTest(extra=extra):
                self.assertIn("Recent use: unknown (no fresh usage census).",
                              self.body(*extra))

    def test_the_workflow_passes_the_published_census(self):
        doc = yaml.safe_load((ROOT / ".github" / "workflows" / "skill-coverage.yml")
                             .read_text(encoding="utf-8"))
        act = next(s for s in doc["jobs"]["retirement"]["steps"]
                   if "scripts/retire_skill.py" in s.get("run", ""))
        self.assertIn('--usage "$GITHUB_WORKSPACE/eval-results/usage/weekly-local.json"',
                      act["run"])


class IssueHistoryTests(Base):
    """Finding 2 (and 11): one issue per skill, ever, and only the bot's."""

    def marker(self, skill=SKILL):
        return retire_skill.ISSUE_MARKER.format(skill=skill)

    def test_the_search_covers_closed_issues(self):
        code, _, err, fake = self.run_main(gates_doc("proposal"))
        self.assertEqual(code, 0, err)
        search = next(args for args, _ in fake.calls
                      if args[0] == "api" and "/issues?" in args[2])
        self.assertIn("state=all", search[2])
        self.assertNotIn("state=open", search[2])

    def test_a_closed_issue_is_already_decided(self):
        fake = FakeGitHub(issues=[issue(42, f"{self.marker()}\nold", state="closed")])
        code, out, err, fake = self.run_main(gates_doc("proposal"), fake=fake)
        self.assertEqual(code, 0, err)
        self.assertEqual(fake.writes(), [])
        self.assertIn("#42", out)
        self.assertIn("already decided", out)

    def test_an_open_issue_is_updated_even_beside_a_closed_one(self):
        fake = FakeGitHub(issues=[issue(3, self.marker(), state="closed"),
                                  issue(42, self.marker())])
        code, _, err, fake = self.run_main(gates_doc("proposal"), fake=fake)
        self.assertEqual(code, 0, err)
        self.assertEqual(fake.writes()[0][:3], ["issue", "edit", "42"])

    def test_the_lowest_open_issue_is_the_one_updated(self):
        fake = FakeGitHub(issues=[issue(90, self.marker()), issue(12, self.marker()),
                                  issue(55, self.marker())])
        code, _, err, fake = self.run_main(gates_doc("proposal"), fake=fake)
        self.assertEqual(code, 0, err)
        self.assertEqual(fake.writes()[0][:3], ["issue", "edit", "12"])

    def test_a_human_issue_quoting_the_marker_neither_suppresses_nor_is_edited(self):
        for state in ("open", "closed"):
            with self.subTest(state=state):
                fake = FakeGitHub(issues=[issue(9, self.marker(), author="someone",
                                                state=state)])
                code, _, err, fake = self.run_main(gates_doc("proposal"), fake=fake)
                self.assertEqual(code, 0, err)
                self.assertEqual([w[:2] for w in fake.writes()], [["issue", "create"]])

    def test_the_jq_filter_returns_author_and_state(self):
        code, _, err, fake = self.run_main(gates_doc("proposal"))
        search = next(args for args, _ in fake.calls
                      if args[0] == "api" and "/issues?" in args[2])
        jq = search[search.index("--jq") + 1]
        self.assertIn(".user.login", jq)
        self.assertIn(".state", jq)

    def test_a_malformed_issue_record_is_refused(self):
        for record in ({"number": "42", "body": self.marker(), "author": BOT,
                        "state": "open"},
                       {"number": True, "body": self.marker(), "author": BOT,
                        "state": "open"},
                       {"number": 42, "body": self.marker(), "author": BOT,
                        "state": "merged"}):
            with self.subTest(record=record):
                code, _, err, fake = self.run_main(gates_doc("proposal"),
                                                   fake=FakeGitHub(issues=[record]))
                self.assertEqual(code, 2)
                self.assertEqual(fake.writes(), [])


class CrossProductTests(Base):
    """Finding 3: every (fixture, roster model) cell exactly once, each no-benefit."""

    def refused(self, cells, mode="proposal"):
        doc = gates_doc(mode, verdicts={SKILL: {"verdict": "retire",
                                                "reason": "r", "cells": cells}})
        code, _, err, fake = self.run_main(doc)
        self.assertEqual(code, 2, err)
        self.assertEqual(fake.writes(), [])
        return err

    def test_a_missing_cell_is_refused(self):
        err = self.refused([cell("basic", m) for m in MODELS] + [cell("edge", MODELS[0])])
        self.assertIn("cross product", err)

    def test_a_duplicate_cell_is_refused(self):
        self.refused([cell("basic", m) for m in MODELS] + [cell("basic", MODELS[0])])

    def test_a_duplicate_standing_in_for_a_missing_cell_is_refused(self):
        self.refused([cell("basic", m) for m in MODELS]
                     + [cell("edge", MODELS[0]), cell("edge", MODELS[0])])

    def test_one_failing_cell_among_many_is_refused(self):
        self.refused([cell("basic", m) for m in MODELS]
                     + [cell("edge", MODELS[0]), cell("edge", MODELS[1], "benefit")])

    def test_a_full_flat_cross_product_retires(self):
        doc = gates_doc("proposal", verdicts={SKILL: {
            "verdict": "retire", "reason": "r", "cells": [cell(None, m) for m in MODELS]}})
        code, _, err, fake = self.run_main(doc)
        self.assertEqual(code, 0, err)
        self.assertIn("| (flat) | claude-opus-5-5 |", fake.calls[-1][1])


class InputTypeTests(Base):
    """Finding 4: a wrong type fails closed with exit 2, never a crash or a pass."""

    def test_wrong_types_in_the_gates_file_are_refused(self):
        good = [cell("basic", m) for m in MODELS]
        docs = {
            "skills a list": {**gates_doc(), "skills": []},
            "roster a string": {**gates_doc(), "roster_models": "claude-opus-5-5"},
            "roster of numbers": {**gates_doc(), "roster_models": [1, 2]},
            "roster mixed": {**gates_doc(), "roster_models": [1, MODELS[0]]},
            "roster duplicated": {**gates_doc(verdicts={SKILL: {
                "verdict": "retire", "reason": "r", "cells": [cell("basic", MODELS[0])]}}),
                "roster_models": [MODELS[0], MODELS[0]]},
            "roster empty string": {**gates_doc(), "roster_models": [""]},
            "as_of a number": {**gates_doc(), "as_of": 20261025},
            "cells a dict": gates_doc(verdicts={SKILL: {"verdict": "retire", "reason": "r",
                                                        "cells": {"a": 1}}}),
            "a model a list": gates_doc(verdicts={SKILL: {"verdict": "retire", "reason": "r",
                                                          "cells": [{**good[0], "model": ["x"]},
                                                                    good[1]]}}),
            "a fixture a number": gates_doc(verdicts={SKILL: {
                "verdict": "retire", "reason": "r",
                "cells": [{**c, "fixture": 3} for c in good]}}),
            "a fixture with markup": gates_doc(verdicts={SKILL: {
                "verdict": "retire", "reason": "r",
                "cells": [{**c, "fixture": "a|b\n@someone"} for c in good]}}),
            "a fixture empty": gates_doc(verdicts={SKILL: {
                "verdict": "retire", "reason": "r",
                "cells": [{**c, "fixture": ""} for c in good]}}),
            "a reason not text": gates_doc(verdicts={SKILL: {"verdict": "retire",
                                                             "reason": {"x": 1},
                                                             "cells": good}}),
            "looks a number": gates_doc(verdicts={SKILL: {
                "verdict": "retire", "reason": "r",
                "cells": [{**c, "looks": 5} for c in good]}}),
        }
        for mode in ("proposal", "auto"):
            for name, doc in docs.items():
                with self.subTest(name, mode=mode):
                    doc = {**doc, "retirement_mode": mode}
                    code, _, err, fake = self.run_main(
                        doc, "--allow-mode", "auto", "--allow-mode", "proposal",
                        env=AUTO_ENV)
                    self.assertEqual(code, 2, err)
                    self.assertIn("retire_skill:", err)
                    self.assertEqual(fake.writes(), [])
                    self.assertEqual(fake.pushes, [])
                    self.assertUntouched()

    def test_a_manifest_that_is_not_an_object_is_refused(self):
        for rel in ("plugin.json", ".claude-plugin/plugin.json"):
            with self.subTest(rel):
                registry = make_registry(self.tmp / rel.replace("/", "_"))
                (registry / "plugins" / PLUGIN / rel).write_text("[1]\n")
                git(registry, "commit", "-qam", "list")
                code, _, err, fake = self.run_main(
                    gates_doc("auto"), "--allow-mode", "auto", env=AUTO_ENV,
                    registries=[f"acsi-agentskills={registry}"])
                self.assertEqual(code, 2, err)
                self.assertEqual(fake.pushes, [])


class AllowModeTests(Base):
    """Finding 5: proposal unless auto is asked for, and auto only on Actions' main."""

    def test_allow_mode_defaults_to_proposal(self):
        code, _, err, fake = self.run_main(gates_doc("auto"), env=AUTO_ENV)
        self.assertEqual(code, 2)
        self.assertIn("has not wired", err)
        self.assertIn("allowed: proposal)", err)
        self.assertEqual(fake.calls, [])
        self.assertUntouched()

    def test_proposal_runs_with_no_allow_mode(self):
        code, _, err, fake = self.run_main(gates_doc("proposal"))
        self.assertEqual(code, 0, err)

    def test_auto_refuses_off_actions_or_off_the_default_branch(self):
        for env in (SCHEDULE,
                    {**AUTO_ENV, "GITHUB_ACTIONS": "True"},
                    {**AUTO_ENV, "GITHUB_ACTIONS": "1"},
                    {k: v for k, v in AUTO_ENV.items() if k != "GITHUB_REF"},
                    {**AUTO_ENV, "GITHUB_REF": "refs/heads/feature"},
                    {**AUTO_ENV, "GITHUB_REF": "refs/pull/1/merge"},
                    {**AUTO_ENV, "GITHUB_REF": "refs/tags/main"}):
            with self.subTest(env=env):
                code, _, err, fake = self.run_main(gates_doc("auto"), "--allow-mode", "auto",
                                                   env=env)
                self.assertEqual(code, 2)
                self.assertIn("auto mode", err)
                self.assertEqual(fake.writes(), [])
                self.assertEqual(fake.pushes, [])
                self.assertUntouched()

    def test_execute_itself_refuses_a_removal_off_actions(self):
        registries = compute_gates.flag_registries([f"acsi-agentskills={self.registry}"])
        fake = FakeGitHub()
        action = retire_skill.plan(gates_doc("auto"), registries, fake.gh, allowed=("auto",))
        for env in (SCHEDULE, {**AUTO_ENV, "GITHUB_REF": "refs/heads/feature"}):
            with self.subTest(env=env):
                with self.assertRaises(retire_skill.RetireError):
                    retire_skill.execute(action, registries, fake.gh, fake.push, env)
                self.assertEqual(fake.writes(), [])
                self.assertEqual(fake.pushes, [])
                self.assertUntouched()

    def test_proposal_does_not_need_the_actions_environment(self):
        code, _, err, _ = self.run_main(gates_doc("proposal"), env=SCHEDULE)
        self.assertEqual(code, 0, err)


class SymlinkTests(AutoTests):
    """Finding 6: never act through a symlink or outside the registry checkout."""

    def relink(self, rel, target):
        """Move `rel` to `target` (both under the registry) and link it back."""
        src, dst = self.registry / rel, self.registry / target
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
        src.symlink_to(Path("../" * (len(Path(rel).parts) - 1) + target))
        git(self.registry, "add", "-A")
        git(self.registry, "commit", "-qm", "link")

    def assertRefused(self, *, proposal_located=False):
        code, _, err, fake = self.run_main(gates_doc("auto"))
        self.assertEqual(code, 2, err)
        self.assertIn("symlink", err)
        self.assertEqual(fake.writes(), [])
        self.assertEqual(fake.pushes, [])
        self.assertEqual(git(self.registry, "status", "--porcelain"), "")
        self.assertEqual(git(self.registry, "branch", "--show-current").strip(), "main")
        code, _, err, fake = self.run_main(gates_doc("proposal"), "--allow-mode", "proposal")
        self.assertEqual(code, 0, err)
        self.assertIn("not located", fake.calls[-1][1])

    def test_a_plugin_directory_symlink_is_refused(self):
        self.relink(f"plugins/{PLUGIN}", f"vendor/{PLUGIN}")
        self.assertRefused()

    def test_a_skills_directory_symlink_is_refused(self):
        self.relink(f"plugins/{PLUGIN}/skills", "shared/skills")
        self.assertRefused()

    def test_a_skill_directory_symlink_is_refused(self):
        self.relink(f"plugins/{PLUGIN}/skills/{SKILL}", f"shared/{SKILL}")
        self.assertRefused()

    def test_a_manifest_symlink_out_of_the_checkout_is_never_written(self):
        outside = self.tmp / "outside.json"
        outside.write_text(json.dumps({"name": PLUGIN, "version": "1.2.7"}, indent=2) + "\n")
        manifest = self.registry / "plugins" / PLUGIN / "plugin.json"
        manifest.unlink()
        manifest.symlink_to(outside)
        git(self.registry, "add", "-A")
        git(self.registry, "commit", "-qm", "link")
        code, _, err, fake = self.run_main(gates_doc("auto"))
        self.assertEqual(code, 2, err)
        self.assertIn("symlink", err)
        self.assertEqual(json.loads(outside.read_text())["version"], "1.2.7")
        self.assertEqual(fake.pushes, [])
        self.assertEqual(git(self.registry, "branch", "--show-current").strip(), "main")

    def test_an_impact_file_symlink_is_never_written(self):
        outside = self.tmp / "impact.md"
        outside.write_text(IMPACT)
        impact = self.registry / "docs" / "skill-impact.md"
        impact.unlink()
        impact.symlink_to(outside)
        git(self.registry, "add", "-A")
        git(self.registry, "commit", "-qm", "link")
        code, _, err, fake = self.run_main(gates_doc("auto"))
        self.assertEqual(code, 2, err)
        self.assertEqual(outside.read_text(), IMPACT)
        self.assertEqual(fake.pushes, [])
        self.assertEqual(git(self.registry, "branch", "--show-current").strip(), "main")


class MutationTests(Base):
    """Finding 7: branches that no earlier test pinned down."""

    def test_a_pr_with_no_head_name_does_not_hold_the_slot(self):
        fake = FakeGitHub(prs=[{"number": 4, "head": None, "url": "u"},
                               {"number": 5, "url": "u"}])
        code, _, err, fake = self.run_main(gates_doc("auto"), "--allow-mode", "auto",
                                           env=AUTO_ENV, fake=fake)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(fake.pushes), 1)

    def test_the_removal_pr_search_reads_open_pulls_of_the_registry(self):
        code, _, err, fake = self.run_main(gates_doc("auto"), "--allow-mode", "auto",
                                           env=AUTO_ENV)
        search = next(args for args, _ in fake.calls if args[0] == "api")
        self.assertEqual(search[2], "repos/example-adopter/acsi-agentskills/pulls"
                                    "?state=open&per_page=100")

    def test_the_run_url_needs_this_repository_and_a_numeric_id(self):
        for env, expected in (
                ({"GITHUB_RUN_ID": "77", "GITHUB_REPOSITORY": "example-adopter/skills-evals"},
                 "actions/runs/77"),
                ({"GITHUB_RUN_ID": "77", "GITHUB_REPOSITORY": "someone/fork"}, None),
                ({"GITHUB_RUN_ID": "77x", "GITHUB_REPOSITORY": "example-adopter/skills-evals"},
                 None),
                ({"GITHUB_REPOSITORY": "example-adopter/skills-evals"}, None)):
            with self.subTest(env=env):
                code, _, err, fake = self.run_main(gates_doc("proposal"),
                                                   env={**SCHEDULE, **env})
                self.assertEqual(code, 0, err)
                body = fake.calls[-1][1]
                if expected:
                    self.assertIn(f"Workflow run: https://github.com/example-adopter/"
                                  f"skills-evals/{expected}", body)
                else:
                    self.assertNotIn("Workflow run:", body)

    def test_a_version_not_written_once_is_refused_before_the_commit(self):
        registry = make_registry(self.tmp / "compact")
        for rel in ("plugin.json", ".claude-plugin/plugin.json"):
            (registry / "plugins" / PLUGIN / rel).write_text(
                json.dumps({"name": PLUGIN, "version": "1.2.7"}, separators=(",", ":")))
        git(registry, "commit", "-qam", "compact")
        code, _, err, fake = self.run_main(gates_doc("auto"), "--allow-mode", "auto",
                                           env=AUTO_ENV,
                                           registries=[f"acsi-agentskills={registry}"])
        self.assertEqual(code, 2)
        self.assertIn("not written once", err)
        self.assertEqual(fake.pushes, [])

    def test_an_unreadable_manifest_is_refused(self):
        registry = make_registry(self.tmp / "nomanifest")
        git(registry, "rm", "-q", f"plugins/{PLUGIN}/.claude-plugin/plugin.json")
        git(registry, "commit", "-qm", "drop")
        code, _, err, fake = self.run_main(gates_doc("auto"), "--allow-mode", "auto",
                                           env=AUTO_ENV,
                                           registries=[f"acsi-agentskills={registry}"])
        self.assertEqual(code, 2)
        self.assertIn(f"cannot read plugins/{PLUGIN}/.claude-plugin/plugin.json", err)

    def test_a_manifest_changed_under_the_plan_is_refused(self):
        gates = gates_doc("auto")
        registries = compute_gates.flag_registries([f"acsi-agentskills={self.registry}"])
        action = retire_skill.plan(gates, registries, FakeGitHub().gh, allowed=("auto",))
        path = self.registry / "plugins" / PLUGIN / "plugin.json"
        path.write_text(json.dumps({"name": PLUGIN, "version": "9.9.9"}, indent=2) + "\n")
        git(self.registry, "commit", "-qam", "race")
        with self.assertRaises(retire_skill.RetireError) as caught:
            retire_skill.prepare_branch(self.registry, action)
        self.assertIn("changed under the plan", str(caught.exception))

    def test_a_figure_that_is_not_a_number_reads_n_a(self):
        self.assertEqual(retire_skill._figure({"retire_upper": "0.1"}, "retire_upper"), "n/a")
        self.assertEqual(retire_skill._figure(None, "retire_upper"), "n/a")
        self.assertEqual(retire_skill._figure({"retire_upper": 0.04449}, "retire_upper"),
                         "0.044")

    def test_a_push_failure_reports_only_its_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "r"
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            git(repo, "commit", "-q", "--allow-empty", "-m", "x")
            git(repo, "remote", "add", "origin", str(Path(tmp) / "missing"))
            with self.assertRaises(retire_skill.RetireError) as caught:
                retire_skill.default_push(repo, "b", "a-token")
            self.assertRegex(str(caught.exception), r"^git push exited \d+$")
            with self.assertRaises(retire_skill.RetireError) as caught:
                retire_skill.default_push(repo, "b")
            self.assertIn("no REGISTRY_TOKEN", str(caught.exception))

    def test_a_push_is_verified_against_the_fetched_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, good, other = Path(tmp) / "r", Path(tmp) / "good.git", Path(tmp) / "other.git"
            for bare in (good, other):
                subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            git(repo, "commit", "-q", "--allow-empty", "-m", "x")
            git(repo, "remote", "add", "origin", str(good))
            retire_skill.default_push(repo, "retire-skill/x", "a-token")
            self.assertEqual(git(good, "rev-parse", "refs/heads/retire-skill/x"),
                             git(repo, "rev-parse", "HEAD"))
            # The push lands in one repository and the fetch reads another, as
            # a refused commit with a "successful" push looks.
            git(repo, "commit", "-q", "--allow-empty", "-m", "y")
            git(repo, "remote", "set-url", "--push", "origin", str(other))
            with self.assertRaises(retire_skill.RetireError) as caught:
                retire_skill.default_push(repo, "retire-skill/x", "a-token")
            self.assertIn("does not hold the removal commit", str(caught.exception))

    def test_the_push_token_is_an_env_header_never_an_argument(self):
        env = retire_skill._auth_env("a-token")
        self.assertEqual(env["GIT_CONFIG_KEY_0"], "http.https://github.com/.extraheader")
        self.assertEqual(env["GIT_CONFIG_VALUE_0"],
                         "AUTHORIZATION: basic eC1hY2Nlc3MtdG9rZW46YS10b2tlbg==")


class SkillImpactTests(unittest.TestCase):
    def test_entry_text(self):
        entry = retire_skill.impact_entry(
            gates_doc("auto"), SKILL, PLUGIN,
            "https://github.com/example-adopter/skills-evals/actions/runs/123",
            ("1.2.7", "2.0.0"), "2026-10-27")
        self.assertEqual(entry, (
            f"## 2026-10-27 — {PLUGIN}/{SKILL} — remove\n\n"
            "- Motivation: the retirement verdict is `retire` "
            "([skills-evals ADR 0014](https://github.com/example-adopter/skills-evals/blob/main/"
            "docs/decisions/0014-removal-pull-requests-merge-without-review.md)): "
            "every cell (2 fixture(s) x 2 roster model(s)) shows no benefit at two fixed "
            "looks.\n"
            f"- Change: `plugins/{PLUGIN}/skills/{SKILL}/` removed, the skill dropped from "
            f"`scripts/plugin_layout.json` and the README table, bundle `{PLUGIN}` 1.2.7 -> "
            "2.0.0 (a removal is a breaking change), by skills-evals' "
            "`scripts/retire_skill.py` (workflow run "
            "https://github.com/example-adopter/skills-evals/actions/runs/123).\n"
            "- Eval: n=84 pooled arm runs (with vs without the skill) as of "
            f"20261025T120000Z, fixtures evals/{SKILL}/basic, evals/{SKILL}/edge, policy "
            "evals/gate-policy.yml; per-cell bounds in the pull request.\n"
            "- Outcome: pending merge; auto-merges once required checks pass, with no "
            "human review ([ADR 0021](https://github.com/example-adopter/acsi-agentskills/"
            "blob/main/docs/decisions/0021-automated-skill-changes-merge-without-review.md)).\n"))

    def test_entry_dates_default_to_as_of_and_flat_fixtures_name_the_skill(self):
        doc = gates_doc("auto", verdicts={SKILL: {"verdict": "retire", "reason": "r",
                                                  "cells": [cell(None, m) for m in MODELS]}})
        entry = retire_skill.impact_entry(doc, SKILL, PLUGIN, None, ("1.0.0", "2.0.0"))
        self.assertTrue(entry.startswith(f"## 2026-10-25 — {PLUGIN}/{SKILL} — remove\n"))
        self.assertIn(f"fixtures evals/{SKILL}, policy", entry)
        self.assertIn("(its scheduled workflow)", entry)
        self.assertIn("n=42 pooled", entry)

    def test_entry_goes_newest_first_after_the_rule(self):
        text = retire_skill.insert_entry(IMPACT, "## NEW — remove\n\n- x\n")
        head, rest = text.split("\n---\n", 1)
        self.assertEqual(rest, "\n## NEW — remove\n\n- x\n\n## 2026-10-23 — "
                               "acsi-coding-anywhere/other-skill — edit\n\n"
                               "- Motivation: an earlier entry.\n")
        self.assertTrue(head.endswith("Header text.\n"))

    def test_a_file_without_the_rule_is_refused(self):
        with self.assertRaises(retire_skill.RetireError):
            retire_skill.insert_entry("# no rule\n", "entry")

    def test_the_auto_commit_carries_the_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = make_registry(Path(tmp) / "r")
            fake = FakeGitHub()
            path = Path(tmp) / "gates.json"
            path.write_text(json.dumps(gates_doc("auto")))
            code = retire_skill.main(
                ["--gates", str(path), "--registry", f"acsi-agentskills={registry}",
                 "--allow-mode", "auto"],
                env={**AUTO_ENV, "GITHUB_RUN_ID": "77",
                     "GITHUB_REPOSITORY": "example-adopter/skills-evals"},
                gh=fake.gh, push=fake.push, out=io.StringIO())
            self.assertEqual(code, 0)
            text = (registry / "docs" / "skill-impact.md").read_text()
            self.assertIn(f"\n---\n\n## 2026-10-25 — {PLUGIN}/{SKILL} — remove\n", text)
            self.assertIn("actions/runs/77", text)
            self.assertIn("## 2026-10-23", text)

    def test_bump(self):
        """A removal raises the major version (the registry's practice for
        removing a skill: sync-skills, 1.1.0 -> 2.0.0)."""
        self.assertEqual(retire_skill.bump("1.2.9"), "2.0.0")
        self.assertEqual(retire_skill.bump("9.0.0"), "10.0.0")
        for bad in ("1.2", "v1.2.3", None, "1.2.3-rc1"):
            with self.assertRaises(retire_skill.RetireError):
                retire_skill.bump(bad)


class GitHubCallTests(unittest.TestCase):
    def test_a_gh_failure_reports_only_its_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_gh = Path(tmp) / "gh"
            fake_gh.write_text("#!/bin/sh\necho 'secret body' ; echo 'secret err' >&2\nexit 4\n")
            fake_gh.chmod(0o755)
            import os
            from unittest import mock
            with mock.patch.dict(os.environ, {"PATH": f"{tmp}:{os.environ['PATH']}"}):
                with self.assertRaises(retire_skill.RetireError) as caught:
                    retire_skill.default_gh(["api", "x"])
            self.assertEqual(str(caught.exception), "gh api exited 4")

    def test_a_token_replaces_gh_token_for_that_call_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_gh = Path(tmp) / "gh"
            fake_gh.write_text('#!/bin/sh\nprintf %s "$GH_TOKEN"\n')
            fake_gh.chmod(0o755)
            import os
            from unittest import mock
            with mock.patch.dict(os.environ, {"PATH": f"{tmp}:{os.environ['PATH']}",
                                              "GH_TOKEN": "repo-token"}):
                self.assertEqual(retire_skill.default_gh(["api", "x"], token="app-token"),
                                 "app-token")
                self.assertEqual(retire_skill.default_gh(["api", "x"]), "repo-token")

    def test_without_registry_token_a_registry_write_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = make_registry(Path(tmp) / "r")
            path = Path(tmp) / "gates.json"
            path.write_text(json.dumps(gates_doc("auto")))
            reads = FakeGitHub()
            err = io.StringIO()
            with redirect_stderr(err):
                code = retire_skill.main(
                    ["--gates", str(path), "--registry", f"acsi-agentskills={registry}",
                     "--allow-mode", "auto"], env=AUTO_ENV, gh=reads.gh, out=io.StringIO())
            self.assertEqual(code, 2)
            self.assertIn("no REGISTRY_TOKEN to push with", err.getvalue())


class RegistryCheckTests(AutoTests):
    """A removal pull request has to pass acsi-agentskills' required checks
    with no human edit, so the branch carries every file those checks read:
    the README skill table, the ADR 0013 layout pin in
    `scripts/plugin_layout.json`, and `skills.lock` when the plugin is locked.
    The registry's own generators produce the first and the last; nothing here
    reimplements them."""

    def plan_for(self, registry=None, mode="auto"):
        doc = gates_doc(mode)
        path = self.write_gates(doc)
        fake = FakeGitHub()
        registries = compute_gates.flag_registries(
            [f"acsi-agentskills={registry or self.registry}"])
        return retire_skill.plan(json.loads(path.read_text()), registries, fake.gh,
                                 None, ("auto", "proposal"))

    def test_the_readme_table_is_regenerated_by_the_registrys_own_script(self):
        code, _, err, _ = self.run_main(gates_doc("auto"))
        self.assertEqual(code, 0, err)
        readme = (self.registry / "README.md").read_text()
        self.assertNotIn(SKILL, readme)
        self.assertIn(KEPT, readme)
        scripts = [call["script"] for call in calls(self.registry)]
        self.assertEqual(scripts[0], "generate_readme_table")
        self.assertEqual(calls(self.registry)[0]["args"], [])

    def test_the_layout_pin_loses_only_the_removed_skill(self):
        code, _, err, _ = self.run_main(gates_doc("auto"))
        self.assertEqual(code, 0, err)
        text = (self.registry / "scripts" / "plugin_layout.json").read_text()
        doc = json.loads(text)
        self.assertEqual(doc["plugins"][PLUGIN]["skills"], [KEPT])
        # The registry's canonical form, so the diff shows only those lines.
        self.assertEqual(text, json.dumps(doc, indent=2) + "\n")

    def test_the_lock_is_repinned_in_a_second_commit(self):
        code, _, err, _ = self.run_main(gates_doc("auto"))
        self.assertEqual(code, 0, err)
        subjects = git(self.registry, "log", "--format=%s", "main..HEAD").split("\n")
        self.assertEqual(len(subjects), 3, subjects)  # two commits and the trailing ""
        self.assertIn("Re-pin skills.lock", subjects[0])
        self.assertIn(f"Remove {PLUGIN}/{SKILL}", subjects[1])
        removal = git(self.registry, "rev-parse", "HEAD~1").strip()
        lock = json.loads((self.registry / "skills.lock").read_text())
        self.assertEqual(lock["ref"], removal)
        self.assertNotIn(f"{PLUGIN}/{SKILL}", lock["skills"])
        self.assertIn(f"{PLUGIN}/{KEPT}", lock["skills"])
        # Only skills.lock moves in the re-pin commit, and the registry's
        # --check-current ran before it was committed.
        self.assertEqual(git(self.registry, "diff", "--name-only", "HEAD~1", "HEAD").split(),
                         ["skills.lock"])
        lock_calls = [c["args"] for c in calls(self.registry) if c["script"].endswith("lock")]
        self.assertEqual(lock_calls, [["--repin"], ["--check-current"]])

    def test_an_unlocked_plugin_gets_no_repin_commit(self):
        registry = make_registry(self.tmp / "unlocked", lock_bundles=("acsi-coding-local",))
        code, _, err, _ = self.run_main(gates_doc("auto"),
                                        registries=[f"acsi-agentskills={registry}"])
        self.assertEqual(code, 0, err)
        self.assertEqual(len(git(registry, "log", "--format=%s", "main..HEAD").split("\n")), 2)
        self.assertEqual([c["script"] for c in calls(registry)], ["generate_readme_table"])

    def test_a_registry_with_no_lock_file_gets_no_repin(self):
        registry = make_registry(self.tmp / "nolock", lock_bundles=None)
        code, _, err, _ = self.run_main(gates_doc("auto"),
                                        registries=[f"acsi-agentskills={registry}"])
        self.assertEqual(code, 0, err)
        self.assertEqual([c["script"] for c in calls(registry)], ["generate_readme_table"])

    def test_a_shallow_checkout_is_refused(self):
        shallow = self.tmp / "shallow"
        subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{self.registry}",
                        str(shallow)], check=True, capture_output=True)
        code, _, err, fake = self.run_main(gates_doc("auto"),
                                           registries=[f"acsi-agentskills={shallow}"])
        self.assertEqual(code, 2)
        self.assertIn("shallow", err)
        self.assertEqual(fake.pushes, [])
        self.assertEqual(calls(shallow), [])

    def test_a_plugins_last_skill_is_refused(self):
        registry = make_registry(self.tmp / "only", skills=((PLUGIN, SKILL),))
        code, _, err, fake = self.run_main(gates_doc("auto"),
                                           registries=[f"acsi-agentskills={registry}"])
        self.assertEqual(code, 2)
        self.assertIn("last skill", err)
        self.assertEqual(fake.pushes, [])
        self.assertEqual(calls(registry), [])
        self.assertTrue((registry / "plugins" / PLUGIN / "skills" / SKILL).is_dir())

    def test_a_layout_file_that_does_not_list_the_skill_is_refused(self):
        for broken in ({"plugins": {PLUGIN: {"defaultEnabled": True, "skills": [KEPT]}}},
                       {"plugins": {}}, {"plugins": [PLUGIN]}, []):
            with self.subTest(repr(broken)[:40]):
                registry = make_registry(self.tmp / f"layout{abs(hash(repr(broken)))}")
                (registry / "scripts" / "plugin_layout.json").write_text(
                    json.dumps(broken, indent=2) + "\n")
                git(registry, "commit", "-qam", "break the layout")
                code, _, err, fake = self.run_main(
                    gates_doc("auto"), registries=[f"acsi-agentskills={registry}"])
                self.assertEqual(code, 2)
                self.assertIn("plugin_layout.json", err)
                self.assertEqual(fake.pushes, [])

    def test_a_missing_layout_file_is_refused(self):
        registry = make_registry(self.tmp / "nolayout")
        (registry / "scripts" / "plugin_layout.json").unlink()
        git(registry, "commit", "-qam", "drop the layout")
        code, _, err, fake = self.run_main(gates_doc("auto"),
                                           registries=[f"acsi-agentskills={registry}"])
        self.assertEqual(code, 2)
        self.assertIn("plugin_layout.json", err)
        self.assertEqual(fake.pushes, [])

    def test_a_missing_generator_is_refused_before_anything_is_written(self):
        for rel in ("scripts/generate_readme_table.py", "scripts/generate_skills_lock.py",
                    "README.md"):
            with self.subTest(rel):
                registry = make_registry(self.tmp / f"gone{rel.count('/')}{rel[-6]}")
                (registry / rel).unlink()
                git(registry, "commit", "-qam", f"drop {rel}")
                code, _, err, fake = self.run_main(
                    gates_doc("auto"), registries=[f"acsi-agentskills={registry}"])
                self.assertEqual(code, 2)
                self.assertIn(rel, err)
                self.assertEqual(fake.pushes, [])
                self.assertEqual(git(registry, "branch", "--show-current").strip(), "main")

    def test_a_generator_failure_reports_only_its_exit_code(self):
        for name in ("generate_readme_table", "generate_skills_lock"):
            with self.subTest(name):
                registry = make_registry(self.tmp / f"fail-{name}")
                Path(f"{registry}.fail-{name}").write_text("x")
                code, _, err, fake = self.run_main(
                    gates_doc("auto"), registries=[f"acsi-agentskills={registry}"])
                self.assertEqual(code, 2)
                self.assertIn(f"scripts/{name}.py", err)
                self.assertIn("exited 3", err)
                self.assertEqual(fake.pushes, [])
                self.assertEqual(fake.writes(), [])

    def test_a_lock_that_does_not_come_current_is_refused(self):
        # --check-current is the registry's own verdict on the re-pin, so a
        # red one refuses rather than pushing a branch CI would reject.
        registry = make_registry(self.tmp / "stale-lock")
        (registry / "scripts" / "generate_skills_lock.py").write_text(
            FAKE_COMMON + 'sys.exit(1 if sys.argv[1:] == ["--check-current"] else 0)\n')
        git(registry, "commit", "-qam", "a re-pin that does not take")
        code, _, err, fake = self.run_main(gates_doc("auto"),
                                           registries=[f"acsi-agentskills={registry}"])
        self.assertEqual(code, 2)
        self.assertIn("--check-current exited 1", err)
        self.assertEqual(fake.pushes, [])

    def test_no_secret_reaches_a_registry_generator(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"GH_TOKEN": "t0ken", "SECRET_PEM": "pem"}):
            code, _, err, _ = self.run_main(gates_doc("auto"))
        self.assertEqual(code, 0, err)
        for call in calls(self.registry):
            self.assertNotIn("GH_TOKEN", call["env"], call["script"])
            self.assertNotIn("SECRET_PEM", call["env"], call["script"])
            self.assertIn("PATH", call["env"])

    def test_the_checkout_is_clean_and_on_the_branch_afterwards(self):
        code, _, err, _ = self.run_main(gates_doc("auto"))
        self.assertEqual(code, 0, err)
        self.assertEqual(git(self.registry, "status", "--porcelain"), "")
        self.assertEqual(git(self.registry, "branch", "--show-current").strip(),
                         f"retire-skill/{SKILL}")

    def test_the_plan_and_the_dry_run_name_every_new_step(self):
        plan = self.plan_for()
        self.assertTrue(plan["repin"])
        self.assertIn("scripts/plugin_layout.json", plan["body"])
        self.assertIn("README.md", plan["body"])
        self.assertIn("skills.lock", plan["body"])
        commands = retire_skill.commands(plan)
        self.assertIn(["python3", "scripts/generate_readme_table.py"], commands)
        self.assertIn(["python3", "scripts/generate_skills_lock.py", "--repin"], commands)
        self.assertIn(["python3", "scripts/generate_skills_lock.py", "--check-current"],
                      commands)
        self.assertEqual(sum(1 for c in commands if c[:2] == ["git", "commit"]), 2)

    def test_a_dry_run_writes_nothing_in_the_registry(self):
        code, out, err, fake = self.run_main(gates_doc("auto"), "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(out)["repin"])
        self.assertEqual(calls(self.registry), [])
        self.assertUntouched()

    def test_an_unlocked_plugins_plan_names_no_repin(self):
        registry = make_registry(self.tmp / "plan-unlocked",
                                 lock_bundles=("acsi-coding-local",))
        plan = self.plan_for(registry)
        self.assertFalse(plan["repin"])
        self.assertNotIn("skills.lock", plan["body"])
        commands = retire_skill.commands(plan)
        self.assertNotIn(["python3", "scripts/generate_skills_lock.py", "--repin"], commands)
        self.assertEqual(sum(1 for c in commands if c[:2] == ["git", "commit"]), 1)

    def test_a_lock_whose_skills_name_the_plugin_counts_as_locked(self):
        # bundles and skills are read together: either one naming the plugin
        # makes the lock stale, and a malformed lock is refused.
        registry = make_registry(self.tmp / "keyed", lock_bundles=("acsi-coding-local",))
        lock = json.loads((registry / "skills.lock").read_text())
        lock["skills"] = {f"{PLUGIN}/{SKILL}": "sha256:" + "0" * 64}
        (registry / "skills.lock").write_text(json.dumps(lock, indent=2) + "\n")
        git(registry, "commit", "-qam", "a lock keyed by skill")
        self.assertTrue(retire_skill.lock_relevant(registry, PLUGIN))
        (registry / "skills.lock").write_text(json.dumps({"bundles": "all"}) + "\n")
        with self.assertRaises(retire_skill.RetireError):
            retire_skill.lock_relevant(registry, PLUGIN)

    def test_a_skill_the_runtime_gate_would_hold_is_refused(self):
        # acsi-agentskills' plugin-runtime-review gate (its ADR 0016) holds a
        # pull request that changes how a plugin runs code until the owner
        # approves the environment. Auto-merge cannot approve it, so such a
        # branch would sit open forever holding the one removal slot.
        cases = {
            "a hooks directory": ("plugins/{p}/skills/{s}/hooks/on-start.sh", "#!/bin/sh\n"),
            "a bin directory": ("plugins/{p}/skills/{s}/bin/tool", "#!/bin/sh\n"),
        }
        for name, (rel, body) in cases.items():
            with self.subTest(name):
                registry = make_registry(self.tmp / f"gated-{rel.count('bin')}")
                path = registry / rel.format(p=PLUGIN, s=SKILL)
                path.parent.mkdir(parents=True)
                path.write_text(body)
                git(registry, "add", "-A")
                git(registry, "commit", "-qm", name)
                code, _, err, fake = self.run_main(
                    gates_doc("auto"), registries=[f"acsi-agentskills={registry}"])
                self.assertEqual(code, 2)
                self.assertIn("plugin-runtime-review", err)
                self.assertEqual(fake.pushes, [])
                self.assertEqual(calls(registry), [])

    # A hook in the skill's own plugin lowers the removal to an issue
    # (AutoTests.test_a_plugin_that_runs_code_falls_back_to_the_issue); one
    # in ANOTHER plugin that names a path inside the skill is still refused.
    HOOKED = "acsi-coding-local"

    def test_a_hook_naming_a_path_inside_the_skill_is_refused(self):
        for rel in (f"plugins/{self.HOOKED}/hooks/hooks.json",
                    f"plugins/{self.HOOKED}/.claude-plugin/plugin.json"):
            with self.subTest(rel):
                registry = make_registry(self.tmp / f"hooked-{rel.count('claude')}",
                                         skills=((PLUGIN, SKILL), (PLUGIN, KEPT),
                                                 (self.HOOKED, "local-skill")))
                path = registry / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                hook = {"type": "command",
                        "command": '"${CLAUDE_PLUGIN_ROOT}"/skills/%s/scripts/run.sh' % SKILL,
                        "timeout": 10}
                if rel.endswith("plugin.json"):
                    doc = json.loads(path.read_text())
                    doc["hooks"] = {"SessionStart": [{"hooks": [hook]}]}
                else:
                    doc = {"hooks": {"SessionStart": [{"hooks": [hook]}]}}
                path.write_text(json.dumps(doc, indent=2) + "\n")
                git(registry, "add", "-A")
                git(registry, "commit", "-qm", "a hook into the skill")
                code, _, err, fake = self.run_main(
                    gates_doc("auto"), registries=[f"acsi-agentskills={registry}"])
                self.assertEqual(code, 2)
                self.assertIn("plugin-runtime-review", err)
                self.assertIn(rel, err)
                self.assertEqual(fake.pushes, [])

    def test_a_hook_naming_another_skill_does_not_refuse(self):
        registry = make_registry(self.tmp / "hooked-elsewhere",
                                 skills=((PLUGIN, SKILL), (PLUGIN, KEPT),
                                         (self.HOOKED, "local-skill")))
        path = registry / "plugins" / self.HOOKED / "hooks" / "hooks.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [
            {"type": "command",
             "command": '"${CLAUDE_PLUGIN_ROOT}"/skills/%s/scripts/run.sh' % KEPT,
             "timeout": 10}]}]}}, indent=2) + "\n")
        git(registry, "add", "-A")
        git(registry, "commit", "-qm", "a hook into the kept skill")
        code, _, err, _ = self.run_main(gates_doc("auto"),
                                        registries=[f"acsi-agentskills={registry}"])
        self.assertEqual(code, 0, err)

    def test_a_waiver_under_the_skill_is_refused(self):
        registry = make_registry(self.tmp / "waived")
        (registry / "scripts" / "skills_waivers.yml").write_text(
            "# A comment naming %s, which is not a waiver.\nwaivers:\n"
            "  - klass: british-spelling\n    registry: acsi-agentskills\n"
            "    path: plugins/%s/skills/%s/SKILL.md\n    issue: 1\n"
            "    reason: invented\n" % (KEPT, PLUGIN, SKILL))
        git(registry, "add", "-A")
        git(registry, "commit", "-qm", "a waiver under the skill")
        code, _, err, fake = self.run_main(gates_doc("auto"),
                                           registries=[f"acsi-agentskills={registry}"])
        self.assertEqual(code, 2)
        self.assertIn("skills_waivers.yml", err)
        self.assertEqual(fake.pushes, [])
        self.assertEqual(calls(registry), [])

    def test_waivers_elsewhere_and_an_empty_list_do_not_refuse(self):
        # The file is parsed, not grepped: a comment naming the skill, a
        # waiver on another skill, and `waivers: []` all pass.
        for name, text in (
                ("empty", "waivers: []\n"),
                ("comment only", "# %s was waived once.\nwaivers: []\n" % SKILL),
                ("another skill", "waivers:\n  - klass: british-spelling\n"
                                  "    registry: acsi-agentskills\n"
                                  "    path: plugins/%s/skills/%s/SKILL.md\n"
                                  "    issue: 1\n    reason: invented\n" % (PLUGIN, KEPT))):
            with self.subTest(name):
                registry = make_registry(self.tmp / f"waiver-{name.replace(' ', '-')}")
                (registry / "scripts" / "skills_waivers.yml").write_text(text)
                git(registry, "add", "-A")
                git(registry, "commit", "-qm", name)
                code, _, err, _ = self.run_main(
                    gates_doc("auto"), registries=[f"acsi-agentskills={registry}"])
                self.assertEqual(code, 0, err)

    def test_a_malformed_waivers_file_is_refused(self):
        for text in ("waivers: not-a-list\n", "waivers:\n  - [\n"):
            with self.subTest(text.strip()[:20]):
                registry = make_registry(self.tmp / f"badwaiver{len(text)}")
                (registry / "scripts" / "skills_waivers.yml").write_text(text)
                git(registry, "add", "-A")
                git(registry, "commit", "-qm", "a malformed waivers file")
                code, _, err, fake = self.run_main(
                    gates_doc("auto"), registries=[f"acsi-agentskills={registry}"])
                self.assertEqual(code, 2)
                self.assertIn("skills_waivers.yml", err)
                self.assertEqual(fake.pushes, [])

    def cite(self, name, citing_rel, citing_text, *, define_elsewhere=None):
        registry = make_registry(self.tmp / name)
        tests = registry / "plugins" / PLUGIN / "skills" / SKILL / "scripts"
        tests.mkdir()
        (tests / "test_example.py").write_text(
            "class Suite:\n    def test_only_the_skill_defines_this(self):\n        pass\n")
        (registry / citing_rel).parent.mkdir(parents=True, exist_ok=True)
        (registry / citing_rel).write_text(citing_text)
        if define_elsewhere:
            (registry / define_elsewhere).write_text(
                "def test_only_the_skill_defines_this():\n    pass\n")
        git(registry, "add", "-A")
        git(registry, "commit", "-qm", name)
        return registry

    def test_a_citation_of_a_test_only_the_skill_defines_is_refused(self):
        # acsi-agentskills' test_every_test_this_repo_cites_by_name_exists
        # fails on a non-test source citing a test nothing defines. Measured:
        # removing skills-doctor leaves .claude/hooks/skills-bootstrap.sh
        # citing a test that lives only in that skill.
        for rel in (".claude/hooks/bootstrap.sh", "scripts/helper.py"):
            with self.subTest(rel):
                registry = self.cite(f"cited-{rel.count('hooks')}", rel,
                                     "# test_only_the_skill_defines_this pins this.\n")
                code, _, err, fake = self.run_main(
                    gates_doc("auto"), registries=[f"acsi-agentskills={registry}"])
                self.assertEqual(code, 2)
                self.assertIn(rel, err)
                self.assertIn("test_only_the_skill_defines_this", err)
                self.assertEqual(fake.pushes, [])
                self.assertEqual(calls(registry), [])

    def test_citations_that_survive_the_removal_do_not_refuse(self):
        cases = {
            # the registry's check does not scan test modules
            "cited only from a test module": ("scripts/test_other.py",
                                              "# test_only_the_skill_defines_this\n", None),
            # another module defines the same name, so it still resolves
            "defined elsewhere too": ("scripts/helper.py",
                                      "# test_only_the_skill_defines_this\n",
                                      "scripts/test_dupe.py"),
            # nor Markdown
            "cited from docs": ("docs/notes.md", "test_only_the_skill_defines_this\n", None),
        }
        for name, (rel, text, elsewhere) in cases.items():
            with self.subTest(name):
                registry = self.cite(name.replace(" ", "-"), rel, text,
                                     define_elsewhere=elsewhere)
                code, _, err, _ = self.run_main(
                    gates_doc("auto"), registries=[f"acsi-agentskills={registry}"])
                self.assertEqual(code, 0, err)

    def test_a_skill_python_file_that_does_not_parse_is_refused(self):
        registry = make_registry(self.tmp / "unparseable")
        bad = registry / "plugins" / PLUGIN / "skills" / SKILL / "broken.py"
        bad.write_text("def (:\n")
        git(registry, "add", "-A")
        git(registry, "commit", "-qm", "unparseable")
        code, _, err, fake = self.run_main(gates_doc("auto"),
                                           registries=[f"acsi-agentskills={registry}"])
        self.assertEqual(code, 2)
        self.assertIn("cannot parse", err)
        self.assertEqual(fake.pushes, [])

    def test_a_proposal_is_filed_whatever_the_registry_checkout_looks_like(self):
        # The registry preconditions are auto-mode's: in proposal mode a human
        # removes, so a shallow or last-skill checkout must still file.
        registry = make_registry(self.tmp / "proposal-only", skills=((PLUGIN, SKILL),))
        code, out, err, fake = self.run_main(
            gates_doc("proposal"), "--allow-mode", "proposal",
            registries=[f"acsi-agentskills={registry}"])
        self.assertEqual(code, 0, err)
        self.assertEqual(fake.writes()[0][:2], ["issue", "create"])
        self.assertEqual(calls(registry), [])


class WorkflowTests(unittest.TestCase):
    """The scheduled wiring, read with a YAML parser, never a line scan."""

    def setUp(self):
        self.doc = yaml.safe_load((ROOT / ".github/workflows/skill-coverage.yml").read_text())
        self.job = self.doc["jobs"]["retirement"]

    def test_runs_only_on_the_schedule_from_main(self):
        condition = self.job["if"]
        self.assertIn("github.event_name == 'schedule'", condition)
        self.assertIn("github.ref == 'refs/heads/main'", condition)
        self.assertNotIn("workflow_dispatch", condition)

    def test_minimal_permissions(self):
        self.assertEqual(self.doc["permissions"], {})
        self.assertEqual(self.job["permissions"], {"contents": "read", "issues": "write"})

    def test_pins_checkouts_and_no_interpolation_in_run(self):
        for step in self.job["steps"]:
            if "uses" in step:
                self.assertIsNone(lock_problem(step["uses"], "skill-coverage.yml"))
                if step["uses"].startswith("actions/checkout@"):
                    self.assertIs(step["with"]["persist-credentials"], False)
                    self.assertNotIn("token", step["with"])
            if "run" in step:
                self.assertNotIn("${{", step["run"])

    def test_auto_mode_is_wired_with_the_app_token_and_a_proposal_fallback(self):
        steps = self.job["steps"]
        act = next(s for s in steps if "scripts/retire_skill.py" in s.get("run", ""))
        self.assertEqual(act["env"], {"GH_TOKEN": "${{ github.token }}",
                                      "REGISTRY_TOKEN": "${{ steps.app-token.outputs.token }}"})
        self.assertIn("modes=(--allow-mode proposal)", act["run"])
        self.assertIn('if [ -n "$REGISTRY_TOKEN" ]; then\n  modes+=(--allow-mode auto)',
                      act["run"])
        self.assertIn("modes+=(--fallback-to-proposal)", act["run"])
        self.assertIn('--date "$(date -u +%F)"', act["run"])
        self.assertNotIn("--dry-run", act["run"])
        mint = steps[steps.index(act) - 1]
        self.assertEqual(mint["id"], "app-token")
        self.assertTrue(mint["uses"].startswith("actions/create-github-app-token@"))
        self.assertIs(mint["continue-on-error"], True)
        self.assertEqual(mint["if"], "steps.credential.outputs.present == 'true'")
        self.assertEqual(mint["with"], {
            "client-id": "${{ secrets.EVAL_IMPROVE_APP_CLIENT_ID }}",
            "private-key": "${{ secrets.EVAL_IMPROVE_APP_PEM }}",
            "owner": "example-adopter", "repositories": "acsi-agentskills",
            "permission-contents": "write", "permission-pull-requests": "write"})
        credential = steps[steps.index(mint) - 1]
        self.assertEqual(credential["id"], "credential")
        self.assertEqual(credential["env"], {"PR_CREDENTIAL_SET": (
            "${{ secrets.EVAL_IMPROVE_APP_CLIENT_ID != '' && "
            "secrets.EVAL_IMPROVE_APP_PEM != '' }}")})
        # The token reaches one step: no other step or the job names it.
        named = [s for s in steps if "steps.app-token" in json.dumps(s) or s.get("id") == "app-token"]
        self.assertEqual(named, [mint, act])
        self.assertNotIn("env", self.job)
        registry = next(s for s in steps if s.get("with", {}).get("repository")
                        == "example-adopter/acsi-agentskills")
        self.assertEqual(registry["with"]["fetch-depth"], 0)
        compute = next(s for s in self.job["steps"]
                       if "scripts/compute_gates.py" in s.get("run", ""))
        self.assertLess(self.job["steps"].index(compute), self.job["steps"].index(act))
        self.assertNotIn("env", compute)
        results = next(s for s in self.job["steps"]
                       if s.get("with", {}).get("ref") == "persistent/eval-results")
        self.assertEqual(results["with"]["path"], "eval-results")


class AdrTests(unittest.TestCase):
    def test_the_adr_is_indexed_and_names_the_matching_adrs(self):
        name = "0014-removal-pull-requests-merge-without-review.md"
        text = (ROOT / "docs" / "decisions" / name).read_text()
        self.assertIn("2026-10-25", text)
        self.assertIn("repo-settings", text)
        self.assertIn("acsi-agentskills", text)
        self.assertEqual(len(list((ROOT / "docs" / "decisions").glob("0014-*.md"))), 1)
        self.assertIn(f"]({name})", (ROOT / "docs" / "decisions" / "README.md").read_text())
        self.assertTrue(retire_skill.ADR.endswith(name))


if __name__ == "__main__":
    unittest.main()
