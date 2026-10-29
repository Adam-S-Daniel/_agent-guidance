#!/usr/bin/env python3
"""Issue #66: several fixtures per skill, N trials per arm, aggregated
statistics — and a default path that writes what it wrote before.

Four things, each tested through the entry point an operator or the workflow
uses (`python3 harness/run_eval.py ...`, `python3 scripts/make_badge.py ...`):

  * THE DEFAULT PRESERVES HISTORICAL OUTPUTS. With no `--trials` (or
    `--trials 1`) a flat fixture's run leaves the historical files byte for
    byte, with `n: 1` appended to each arm's summary.json, plus the additive
    tool trace artifact. Compared against a golden tree that `main`'s own
    harness wrote (see `TestIssue66SingleTrialIsMainPlusN`).
  * LAYOUT. A skill directory holds one flat fixture or nested ones, never
    both; every nested fixture runs, or the one `--fixture` selects; and a
    nested fixture's results go under its own directory however it was
    named on the command line.
  * TRIALS. `--trials N` writes N trial directories and an aggregate whose
    `n` counts every trial, errored or not.
  * THE BADGE reads both shapes, averages over every trial of every fixture
    of every run in its window, and prints for the published shapes exactly
    the message it printed before.

Hermetic. Every CLI call a test makes reaches test/fake-claude: directly, or
through `SCRIPTED_CLI` below, which hands each call to test/fake-claude and
only decides which canned mode it answers in and which canned scores the
judge reports. No test sleeps, reads the clock (the run directory's name is
injected with `--timestamp`), or touches the network.

A child process gets the environment `_child_env` builds and nothing else:
the operator's `PATH` (to find python3 and git) and values this module
writes. `HOME` and `TMPDIR` point inside the test's own `mkdtemp`, and so do
the results directory and the scripted CLI's notes, so the harness's
workspaces and everything a run writes land under it and are removed with
it; `PYTHONDONTWRITEBYTECODE` keeps a child from leaving `__pycache__` in the
checkout.

Discovered and run by test/run_tests.py; also runnable on its own with
`python3 test/issues/test_issue_66.py`.
"""

from __future__ import annotations

import argparse
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
HARNESS_DIR = REPO_ROOT / "harness"
SCRIPTS_DIR = REPO_ROOT / "scripts"
FAKE_CLAUDE = TEST_DIR / "fake-claude"
FAKE_REGISTRY = TEST_DIR / "fixtures" / "fake_registry"
GOLDEN_FIXTURE = TEST_DIR / "fixtures" / "issue_66" / "fixture-primary-skill"
GOLDEN_RESULTS = TEST_DIR / "fixtures" / "issue_66" / "golden"

sys.path.insert(0, str(HARNESS_DIR))
import run_eval  # noqa: E402

REGISTRY_URL = "https://github.com/example-adopter/acsi-agentskills"
TS = "20260716T070000Z"
# The PATH and mount table every arm here runs with, explicit: the arm's read
# fence is built from both, so no result depends on the host's (a WSL PATH
# under /mnt/c, its mountinfo). An empty mount table names no alias.
TEST_PATH = os.pathsep.join(dict.fromkeys(
    (str(Path(sys.executable).parent), "/usr/local/bin", "/usr/bin", "/bin")))
DATE = "2026-07-16"

# One check that passes on the untouched seed and one that passes only when
# the agent wrote `marker.txt` — which the scripted CLI does on the trials a
# test tells it to, so a pass rate below 1 is something a test can construct.
CHECKS = [
    {"id": "readme-kept", "type": "file_count", "paths": ["README.md"],
     "min": 1, "max": 1},
    {"id": "marker-written", "type": "file_count", "paths": ["marker.txt"],
     "min": 1},
]

# A stand-in for `claude` that SEQUENCES test/fake-claude. Its configuration
# sits beside it as cli.json: the fake's path, and one list of steps for the
# agent calls and one for the judge calls, consumed in call order.
#
#   agent step   {}                        answer as the fake's `agent` mode
#                {"write": "marker.txt"}   ...after writing that file in cwd
#                {"mode": "error"}         answer as the fake's `error` mode
#   judge step   {"overall": 7.5, "scores": {"Completeness": 5}}
#                                          the fake's `judge` answer, with
#                                          these numbers in place of its own
#                {"fail": true}            exit 1 without answering
#
# An agent call is the one carrying `--setting-sources` (run_agent passes it;
# the judge call does not). A call past the end of its list exits 3, so a run
# that makes more calls than the test scripted fails instead of passing on a
# default. Every call is appended to calls.jsonl with its cwd.
SCRIPTED_CLI = '''\
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = json.loads((HERE / "cli.json").read_text(encoding="utf-8"))
FAKE = CONFIG["fake"]

if "--version" in sys.argv:
    os.execv(FAKE, [FAKE] + sys.argv[1:])

# An arm names a setting source; the judge passes an EMPTY one.
sources = (sys.argv[sys.argv.index("--setting-sources") + 1]
           if "--setting-sources" in sys.argv[:-1] else "")
kind = "agents" if sources else "judges"
state_path = HERE / "state.json"
state = (json.loads(state_path.read_text(encoding="utf-8"))
         if state_path.exists() else {"agents": 0, "judges": 0})
index = state[kind]
state[kind] += 1
state_path.write_text(json.dumps(state), encoding="utf-8")
with open(HERE / "calls.jsonl", "a", encoding="utf-8") as handle:
    handle.write(json.dumps({"kind": kind, "cwd": os.getcwd()}) + "\\n")

steps = CONFIG[kind]
if index >= len(steps):
    sys.stderr.write(f"unscripted {kind} call number {index + 1}\\n")
    sys.exit(3)
step = steps[index]

if kind == "agents":
    if step.get("write"):
        Path(step["write"]).write_text("written by the scripted agent\\n",
                                       encoding="utf-8")
    os.environ["FAKE_CLAUDE_MODE"] = step.get("mode", "agent")
    os.execv(FAKE, [FAKE] + sys.argv[1:])

if step.get("fail"):
    sys.stderr.write("scripted judge failure\\n")
    sys.exit(1)
os.environ["FAKE_CLAUDE_MODE"] = "judge"
done = subprocess.run([FAKE] + sys.argv[1:], input=sys.stdin.read(),
                      capture_output=True, text=True, check=True)
payload = json.loads(done.stdout)
verdict = json.loads(payload["result"])
verdict["overall"] = step["overall"]
for dimension in verdict["dimensions"]:
    if dimension["name"] in step.get("scores", {}):
        dimension["score"] = step["scores"][dimension["name"]]
payload["result"] = json.dumps(verdict)
print(json.dumps(payload))
'''


def _child_env(tmp: Path, **extra: str) -> dict:
    """The WHOLE environment of a child process a test starts.

    Built, not inherited: nothing comes from the operator, so no
    `$EVAL_ROSTER`, `$SKILLS_EVALS_REGISTRIES`, `$AGENTSKILLS_DIR` or
    `$CLAUDE_BIN` of theirs can decide a result. `PATH` is `TEST_PATH` and
    the mount table an empty fixture, so the arm's read fence does not
    depend on the host's either. `HOME` and `TMPDIR` are inside `tmp`. Git's auto-maintenance is off: the harness commits in a
    workspace it then deletes, and a detached `git maintenance` would race
    that (test/run_tests.py, `without_git_auto_maintenance`).
    """
    home, scratch = tmp / "home", tmp / "scratch"
    home.mkdir(exist_ok=True)
    scratch.mkdir(exist_ok=True)
    mountinfo = tmp / "mountinfo"
    mountinfo.write_text("", encoding="utf-8")
    env = {"PATH": TEST_PATH, run_eval.MOUNTINFO_ENV: str(mountinfo),
           "HOME": str(home),
           "TMPDIR": str(scratch), "LANG": "C.UTF-8",
           "PYTHONDONTWRITEBYTECODE": "1",
           "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "maintenance.auto",
           "GIT_CONFIG_VALUE_0": "false"}
    env.update(extra)
    return env


def _write_fixture(directory: Path, skill: str, **extra) -> Path:
    """A small skill fixture at `directory`: fixture.yaml plus a one-file
    seed. Both models are pinned, so no roster is read."""
    (directory / "seed").mkdir(parents=True)
    (directory / "seed" / "README.md").write_text("# seed\n", encoding="utf-8")
    doc = {"skill": skill, "registry": REGISTRY_URL,
           "model": "fake-agent-model", "judge": {"model": "fake-judge-model"},
           "prompt": f"Do the {directory.name} task.",
           "judge_rubric": "Score each dimension from 0 to 10.",
           "objective_checks": CHECKS}
    doc.update(extra)
    (directory / "fixture.yaml").write_text(
        yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return directory


def _tree(root: Path) -> list[str]:
    """Every file under `root`, as sorted relative POSIX paths."""
    return sorted(p.relative_to(root).as_posix()
                  for p in root.rglob("*") if p.is_file())


def _stable_bytes(path: Path) -> bytes:
    """A result file's bytes without what differs from one run to the next
    whatever the harness does: a summary's `run` and `usage` blocks (#370:
    the harness commit, wall-clock times) and the report's two lines made
    from them. Both blocks are a summary's last two keys, so the rest
    re-serializes to the bytes the harness wrote before them. Also without
    `judge_selection` (#371), the key before those two, which names the judge
    chosen and is tested in test_issue_371_judge_tier.py, and without
    `fixture_digest` and `judge_conditional` (#70), which follow it and are
    tested in test_issue_70_retirement.py."""
    if path.name == "summary.json":
        summary = json.loads(path.read_text(encoding="utf-8"))
        for block in ("run", "usage", "judge_selection"):
            summary.pop(block)
        for block in ("fixture_digest", "judge_conditional"):
            summary.pop(block, None)
        return json.dumps(summary, indent=2).encode("utf-8")
    if path.name == "report.md":
        return b"".join(line for line in path.read_bytes().splitlines(keepends=True)
                        if not line.startswith((b"- Run: ", b"- Account meter")))
    return path.read_bytes()


class _HarnessCase(unittest.TestCase):
    """A scratch directory, and the two entry points run inside it."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.results = self.tmp / "results"
        self.evals = self.tmp / "evals"
        self.cli_dir = self.tmp / "cli"

    def _script(self, agents: list, judges: list | None = None) -> None:
        """Install the scripted CLI for this test's next run(s)."""
        self.cli_dir.mkdir(exist_ok=True)
        (self.cli_dir / "cli.json").write_text(json.dumps(
            {"fake": str(FAKE_CLAUDE), "agents": agents,
             "judges": judges or []}), encoding="utf-8")
        cli = self.cli_dir / "claude"
        cli.write_text(f"#!/usr/bin/env python3\n{SCRIPTED_CLI}",
                       encoding="utf-8")
        cli.chmod(0o755)

    def _calls(self, kind: str | None = None) -> list[dict]:
        """The scripted CLI's calls so far (`--version` probes excluded)."""
        log = self.cli_dir / "calls.jsonl"
        if not log.exists():
            return []
        calls = [json.loads(line) for line in
                 log.read_text(encoding="utf-8").splitlines()]
        return [c for c in calls if kind is None or c["kind"] == kind]

    def _registry(self, skill: str) -> Path:
        """A registry checkout under the scratch dir carrying `skill`."""
        skill_dir = self.tmp / "registry" / "plugins" / "b" / "skills" / skill
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {skill}\ndescription: stand-in.\n---\n",
            encoding="utf-8")
        return self.tmp / "registry"

    def _run(self, eval_dir: Path, *flags: str, results: Path | None = None,
             cli: Path | None = None, mode: str | None = None):
        """`python3 harness/run_eval.py <eval_dir> <flags>`, from the repo
        root, with `--timeout 60` and the scripted CLI unless `cli` names
        another."""
        extra = {"CLAUDE_BIN": str(cli or self.cli_dir / "claude")}
        if mode:
            extra["FAKE_CLAUDE_MODE"] = mode
        cmd = [sys.executable, str(HARNESS_DIR / "run_eval.py"), str(eval_dir),
               "--results-dir", str(results or self.results),
               "--timeout", "60", *flags]
        return subprocess.run(cmd, capture_output=True, text=True,
                              env=_child_env(self.tmp, **extra),
                              cwd=str(REPO_ROOT), timeout=600)

    def _json(self, *parts: str, results: Path | None = None) -> dict:
        path = (results or self.results).joinpath(*parts)
        return json.loads(path.read_text(encoding="utf-8"))

    def _refused(self, proc, *needles: str) -> None:
        """Exit 2, the named reason on stdout, no traceback, nothing written
        and no agent or judge call made."""
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        for needle in needles:
            self.assertIn(needle, proc.stdout)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertFalse(self.results.exists(),
                         "a refused run must write nothing under results/")
        self.assertEqual(self._calls(), [])


# ---------------------------------------------------------------------------
# 1. the default path


class TestIssue66SingleTrialIsMainPlusN(_HarnessCase):
    """The single-fixture, single-trial run of a flat fixture writes what
    `main` wrote, plus `n: 1` and a separate tool trace artifact.

    THE GOLDEN TREE under test/fixtures/issue_66/golden/ was written by
    `main`'s harness, not by this branch: `harness/run_eval.py` as of
    ed135aa0bcb0d84452212ca13624af868e4d5f9f (the commit this branch started
    from), run on test/fixtures/issue_66/fixture-primary-skill with

        CLAUDE_BIN=test/fake-claude FAKE_CLAUDE_MODE=judge \\
          python3 harness/run_eval.py <fixture> --arm both --timeout 60 \\
            --registry acsi-agentskills=test/fixtures/fake_registry \\
            --results-dir <out>

    and copied in unedited, with one later edit: #71 added
    `"permission_mode": "auto"` to each summary's `harness` block, the only
    byte that harness change makes in this tree (regenerating would also
    bake in `n`, which this test appends itself). Per-model token reporting
    and explicit effort later added `"effort": null` to the `harness` block
    and the `model_tokens` and `cross_model` fields, regenerated with the
    command below and with `n` removed. The `run` and `usage` blocks (#370)
    are not in it: they name the harness commit and the wall clock, so the
    comparison leaves them out (`_stable_bytes`). Its run directory is named for the second that
    command ran in, which is the timestamp these tests inject. To regenerate
    it, repeat that command from a checkout of the commit to compare against.

    What it pins is this harness's plumbing. The check details and the
    judge's dimensions inside it come from harness/scorers/ and
    test/fake-claude, so a deliberate change to either — a reworded
    `file_count` detail, a new canned score — changes these bytes too, and
    the golden is then regenerated rather than hand-edited.
    """

    GOLDEN_TS = "20261004T054008Z"
    N_SUFFIX = b',\n  "n": 1\n}'

    def _run_golden(self, results: Path, *flags: str):
        return self._run(GOLDEN_FIXTURE, "--arm", "both",
                         "--registry", f"acsi-agentskills={FAKE_REGISTRY}",
                         "--timestamp", self.GOLDEN_TS, *flags,
                         results=results, cli=FAKE_CLAUDE, mode="judge")

    def test_the_run_leaves_mains_files_with_n_appended_to_each_summary(self):
        proc = self._run_golden(self.results)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        # Historical files plus one trace per arm: no trial directory at n = 1.
        traces = [f"fixture-primary-skill/{self.GOLDEN_TS}/{arm}/transcripts/tool_trace.json"
                  for arm in ("with_skill", "without_skill")]
        self.assertEqual(_tree(self.results), sorted(_tree(GOLDEN_RESULTS) + traces))
        for rel in traces:
            with self.subTest(trace=rel):
                evidence = json.loads((self.results / rel).read_text(encoding="utf-8"))
                self.assertEqual(evidence["schema_version"], 1)
                self.assertIs(evidence["complete"], False)
                self.assertEqual(evidence["calls"], 1)
                self.assertEqual(evidence["events"], [])
                self.assertEqual(evidence["omitted_events"], 0)
        summaries = 0
        for rel in _tree(GOLDEN_RESULTS):
            golden = (GOLDEN_RESULTS / rel).read_bytes()
            written = _stable_bytes(self.results / rel)
            with self.subTest(file=rel):
                if not rel.endswith("/summary.json"):
                    self.assertEqual(written, golden)
                    continue
                summaries += 1
                # `main`'s bytes up to its closing brace, then `n`, then the
                # brace: every field `main` wrote is there unchanged, in
                # order, and `n` is the only addition.
                self.assertTrue(golden.endswith(b"\n}"), golden[-20:])
                self.assertEqual(written, golden[:-len(b"\n}")] + self.N_SUFFIX)
        self.assertEqual(summaries, 2, "the golden covers both arms")

    def test_trials_1_is_the_default(self):
        default, explicit = self.tmp / "default", self.tmp / "explicit"
        for results, flags in ((default, ()), (explicit, ("--trials", "1"))):
            proc = self._run_golden(results, *flags)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(_tree(explicit), _tree(default))
        for rel in _tree(default):
            with self.subTest(file=rel):
                self.assertEqual(_stable_bytes(explicit / rel),
                                 _stable_bytes(default / rel))

    def test_the_documented_objective_only_invocation_still_works(self):
        # The issue's own verifier line, on the fixture the scheduled run
        # uses: `--trials 1` beside objective-only prints what no flag does.
        eval_dir = (REPO_ROOT / "test" / "fixtures" / "evals" / "workflow-path-audit"
                    / "required-check-routing")
        plain = self._run(eval_dir, "--arm", "objective-only")
        flagged = self._run(eval_dir, "--arm", "objective-only",
                            "--trials", "1")
        self.assertEqual(plain.returncode, 1, plain.stdout + plain.stderr)
        self.assertEqual(json.loads(plain.stdout)["skill"],
                         "workflow-path-audit")
        self.assertEqual((flagged.returncode, flagged.stdout),
                         (plain.returncode, plain.stdout))


# ---------------------------------------------------------------------------
# 2. layout


class TestIssue66NestedFixtures(_HarnessCase):
    """`evals/<skill>/<name>/fixture.yaml`: discovery, `--fixture`, and
    results that never share an arm directory."""

    SKILL = "multi-skill"
    ARM = ("--arm", "without_skill", "--no-judge", "--timestamp", TS)

    def setUp(self):
        super().setUp()
        self.skill_dir = self.evals / self.SKILL
        for name in ("alpha", "beta"):
            _write_fixture(self.skill_dir / name, self.SKILL)
        self.run_dir = self.results / self.SKILL / TS

    def test_a_two_fixture_skill_runs_both_and_writes_both_directories(self):
        self._script(agents=[{"write": "marker.txt"}, {}])
        proc = self._run(self.skill_dir, *self.ARM)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(sorted(p.name for p in self.run_dir.iterdir()),
                         ["alpha", "beta", "report.md"])
        # Name order, one agent call each; alpha's wrote the marker.
        marker = {}
        for name in ("alpha", "beta"):
            summary = self._json(self.SKILL, TS, name, "without_skill",
                                 "summary.json")
            self.assertEqual((summary["skill"], summary["fixture"],
                              summary["n"]), (self.SKILL, name, 1))
            self.assertIsNone(summary["error"])
            marker[name] = {c["id"]: c["passed"]
                            for c in summary["objective_checks"]}["marker-written"]
        self.assertEqual(marker, {"alpha": True, "beta": False})
        report = (self.run_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn("## Fixture: alpha (n=1)", report)
        self.assertIn("## Fixture: beta (n=1)", report)
        # Each section carries its own fixture's prompt, in fixture order.
        self.assertLess(report.index("Do the alpha task."),
                        report.index("## Fixture: beta (n=1)"))
        self.assertLess(report.index("## Fixture: beta (n=1)"),
                        report.index("Do the beta task."))

    def test_fixture_selects_one(self):
        self._script(agents=[{}])
        proc = self._run(self.skill_dir, "--fixture", "beta", *self.ARM)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(sorted(p.name for p in self.run_dir.iterdir()),
                         ["beta", "report.md"])
        self.assertEqual(len(self._calls("agents")), 1)
        report = (self.run_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn("## Fixture: beta (n=1)", report)
        self.assertNotIn("alpha", report)

    def test_a_nested_fixture_named_by_its_own_directory_writes_the_same_tree(self):
        # `run_eval.py evals/<skill>/<name>` is how a nested fixture is
        # dispatched today. It must land where `--fixture <name>` lands, and
        # never in the flat `<run>/<arm>/` every fixture of the skill shares.
        by_leaf, by_flag = self.tmp / "by-leaf", self.tmp / "by-flag"
        self._script(agents=[{}, {}])
        leaf = self._run(self.skill_dir / "alpha", *self.ARM, results=by_leaf)
        flag = self._run(self.skill_dir, "--fixture", "alpha", *self.ARM,
                         results=by_flag)
        self.assertEqual((leaf.returncode, flag.returncode), (0, 0),
                         leaf.stdout + leaf.stderr + flag.stdout + flag.stderr)
        self.assertEqual(_tree(by_leaf), [
            f"{self.SKILL}/{TS}/alpha/without_skill/summary.json",
            f"{self.SKILL}/{TS}/alpha/without_skill/transcripts/raw.json",
            f"{self.SKILL}/{TS}/alpha/without_skill/transcripts/tool_trace.json",
            f"{self.SKILL}/{TS}/report.md"])
        self.assertEqual(_tree(by_flag), _tree(by_leaf))
        for rel in _tree(by_leaf):
            with self.subTest(file=rel):
                self.assertEqual(_stable_bytes(by_flag / rel),
                                 _stable_bytes(by_leaf / rel))

    def test_two_fixtures_run_one_at_a_time_keep_separate_results(self):
        # The defect: both wrote results/<skill>/<ts>/<arm>/summary.json, so
        # the second run's summary replaced the first's.
        self._script(agents=[{"write": "marker.txt"}, {}])
        for name in ("alpha", "beta"):
            proc = self._run(self.skill_dir / name, *self.ARM)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertFalse((self.run_dir / "without_skill").exists())
        passed = {}
        for name in ("alpha", "beta"):
            summary = self._json(self.SKILL, TS, name, "without_skill",
                                 "summary.json")
            self.assertEqual(summary["fixture"], name)
            passed[name] = sum(c["passed"] for c in summary["objective_checks"])
        self.assertEqual(passed, {"alpha": 2, "beta": 1})

    def test_a_flat_fixture_and_nested_ones_together_are_refused(self):
        # From either side: the skill directory (now itself a fixture), and
        # a nested fixture whose parent holds a fixture.yaml.
        shutil.copy(self.skill_dir / "alpha" / "fixture.yaml",
                    self.skill_dir / "fixture.yaml")
        self._script(agents=[{}])
        for eval_dir in (self.skill_dir, self.skill_dir / "alpha"):
            with self.subTest(eval_dir=eval_dir.name):
                self._refused(self._run(eval_dir, *self.ARM),
                              "fixture configuration error:", "never both")

    def test_a_fixture_inside_a_nested_fixture_is_refused(self):
        # alpha/inner is a fixture directly inside the fixture alpha. Named
        # as the skill directory, as alpha, or as inner, it is the same
        # mixed layout and is refused.
        _write_fixture(self.skill_dir / "alpha" / "inner", self.SKILL)
        self._script(agents=[{}, {}, {}])
        for eval_dir in (self.skill_dir, self.skill_dir / "alpha",
                         self.skill_dir / "alpha" / "inner"):
            with self.subTest(eval_dir=eval_dir.name):
                self._refused(self._run(eval_dir, *self.ARM),
                              "fixture configuration error:", "never both")

    def test_the_position_is_the_path_as_named_not_what_it_links_to(self):
        # A skill directory reached through a symlink, and holding two
        # entries that link to directories of the SAME name elsewhere. The
        # fixtures are the entries: `one` and `two`, under the skill the
        # link is named for — not `shared` twice, and not flat.
        for holder in ("x", "y"):
            _write_fixture(self.tmp / holder / "shared", "linked-skill")
        real = self.tmp / "impl"
        real.mkdir()
        (real / "one").symlink_to(self.tmp / "x" / "shared")
        (real / "two").symlink_to(self.tmp / "y" / "shared")
        (self.evals / "linked-skill").symlink_to(real)
        linked = self.evals / "linked-skill"
        by_dir, by_leaf = self.tmp / "by-dir", self.tmp / "by-leaf"
        self._script(agents=[{}] * 4)
        proc = self._run(linked, *self.ARM, results=by_dir)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        for name in ("one", "two"):
            proc = self._run(linked / name, *self.ARM, results=by_leaf)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        expected = sorted(
            [f"linked-skill/{TS}/report.md"]
            + [f"linked-skill/{TS}/{name}/without_skill/{leaf}"
               for name in ("one", "two")
               for leaf in ("summary.json", "transcripts/raw.json", "transcripts/tool_trace.json")])
        self.assertEqual(_tree(by_dir), expected)
        self.assertEqual(_tree(by_leaf), expected)

    def test_an_entry_linking_to_another_skills_fixture_is_refused(self):
        _write_fixture(self.tmp / "elsewhere" / "another-skill" / "theirs",
                       "another-skill")
        (self.skill_dir / "gamma").symlink_to(
            self.tmp / "elsewhere" / "another-skill" / "theirs")
        self._script(agents=[{}, {}, {}])
        self._refused(self._run(self.skill_dir, *self.ARM),
                      "declares `skill: another-skill`",
                      f"nested fixture of {self.SKILL!r}")

    def test_a_flat_fixtures_seed_is_not_a_nested_fixture(self):
        # A seed may contain anything, a fixture.yaml included; `seed/` is
        # the flat fixture's own and is not read as a second fixture.
        flat = _write_fixture(self.evals / "flat-skill", "flat-skill")
        shutil.copy(flat / "fixture.yaml", flat / "seed" / "fixture.yaml")
        self._script(agents=[{}])
        proc = self._run(flat, *self.ARM)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        summary = self._json("flat-skill", TS, "without_skill", "summary.json")
        self.assertNotIn("fixture", summary)
        self.assertEqual(summary["n"], 1)

    def test_the_rule_is_positional(self):
        # Nested means "my parent directory is named for my skill" and
        # nothing else. The same fixture file is flat in a directory whose
        # parent is not named for its skill, and nested in one whose parent
        # is — including the corner the harness documents, a directory that
        # shares its parent's (and the skill's) name.
        self._script(agents=[{}, {}])
        flat = _write_fixture(self.evals / "elsewhere", "twin")
        nested = _write_fixture(self.evals / "twin" / "twin", "twin")
        for eval_dir in (flat, nested):
            proc = self._run(eval_dir, *self.ARM)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(
            sorted(p.name for p in (self.results / "twin" / TS).iterdir()),
            ["report.md", "twin", "without_skill"])
        self.assertNotIn("fixture", self._json("twin", TS, "without_skill",
                                               "summary.json"))
        self.assertEqual(self._json("twin", TS, "twin", "without_skill",
                                    "summary.json")["fixture"], "twin")

    def test_a_fixture_flag_that_selects_nothing_is_refused(self):
        self._script(agents=[{}])
        flat = _write_fixture(self.evals / "flat-skill", "flat-skill")
        empty = self.evals / "empty"
        empty.mkdir()
        cases = (
            ((self.skill_dir, "--fixture", "gamma"),
             ("names no fixture under", "alpha, beta")),
            ((flat, "--fixture", "alpha"), ("is itself a fixture",)),
            ((empty,), ("holds no fixture.yaml",)),
            ((self.evals / "absent",), ("holds no fixture.yaml",)),
        )
        for argv, needles in cases:
            with self.subTest(argv=[str(a) for a in argv[1:]] or argv[0].name):
                self._refused(self._run(*argv, *self.ARM),
                              "fixture configuration error:", *needles)

    def test_a_fixture_name_that_cannot_be_a_results_directory_is_refused(self):
        # A nested fixture's directory sits beside report.md, where a flat
        # fixture's arm directories go; `seed` is a flat fixture's own.
        self._script(agents=[{}])
        for name in ("with_skill", "without_skill", "objective-only",
                     "report.md", "Report.md", "seed", "has space"):
            with self.subTest(name=name):
                skill_dir = self.evals / f"skill-{len(name)}-{ord(name[0])}"
                skill = skill_dir.name
                _write_fixture(skill_dir / name, skill)
                # As a discovered fixture, and named by its own directory.
                for eval_dir in (skill_dir, skill_dir / name):
                    self._refused(self._run(eval_dir, *self.ARM),
                                  "fixture configuration error:",
                                  f"invalid fixture name {name!r}")

    def test_a_nested_fixture_of_another_skill_is_refused(self):
        _write_fixture(self.skill_dir / "gamma", "another-skill")
        self._script(agents=[{}])
        self._refused(self._run(self.skill_dir, *self.ARM),
                      "declares `skill: another-skill`",
                      f"nested fixture of {self.SKILL!r}")

    def test_every_fixture_is_checked_before_any_arm_runs(self):
        # A third fixture, beta2, is refused — a RECORDED refusal, so it
        # leaves its artifacts, under its own directory. alpha and beta are
        # fine and sort first, and neither is run.
        _write_fixture(self.skill_dir / "beta2", self.SKILL, judge=["x"])
        self._script(agents=[{}, {}, {}])
        proc = self._run(self.skill_dir, "--arm", "without_skill",
                         "--timestamp", TS)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("invalid_judge_block:", proc.stdout)
        self.assertEqual(self._calls(), [])
        self.assertEqual(_tree(self.results), [
            f"{self.SKILL}/{TS}/beta2/without_skill/summary.json",
            f"{self.SKILL}/{TS}/report.md"])
        summary = self._json(self.SKILL, TS, "beta2", "without_skill",
                             "summary.json")
        self.assertEqual(summary["error"]["type"], "invalid_judge_block")
        self.assertEqual(summary["fixture"], "beta2")
        # No trial was attempted, so there is no trial count to state.
        self.assertNotIn("n", summary)
        report = (self.run_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn(f"# Eval report: {self.SKILL}/beta2", report)

    def test_objective_only_scores_one_fixture(self):
        self._script(agents=[])
        both = self._run(self.skill_dir, "--arm", "objective-only")
        self._refused(both, "--arm objective-only scores one fixture",
                      "alpha, beta", "--fixture NAME")
        selected = self._run(self.skill_dir, "--arm", "objective-only",
                             "--fixture", "alpha")
        leaf = self._run(self.skill_dir / "alpha", "--arm", "objective-only")
        # Pristine seed: readme-kept passes, marker-written fails.
        self.assertEqual(selected.returncode, 1,
                         selected.stdout + selected.stderr)
        payload = json.loads(selected.stdout)
        self.assertEqual(sorted(payload), ["arm", "checks", "skill"])
        self.assertEqual([c["passed"] for c in payload["checks"]],
                         [True, False])
        self.assertEqual((leaf.returncode, leaf.stdout),
                         (selected.returncode, selected.stdout))

    def test_an_errored_arm_of_a_nested_fixture_is_named_with_its_fixture(self):
        self._script(agents=[{}, {"mode": "error"}])
        proc = self._run(self.skill_dir, *self.ARM)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("Runner-level error in arm(s): beta/without_skill\n",
                      proc.stdout)
        report = (self.run_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn("- without_skill trial 1: nonzero_exit:", report)

    def test_a_guidance_fixture_is_not_discovered_or_trialed(self):
        guidance_dir = self.evals / "guidance" / "some-section"
        guidance_dir.mkdir(parents=True)
        (guidance_dir / "fixture.yaml").write_text(yaml.safe_dump(
            {"subject": "guidance", "section": "some-section",
             "prompt": "Do it."}), encoding="utf-8")
        self._script(agents=[{}])
        cases = ((self.evals / "guidance", "--arm", "both"),
                 (guidance_dir, "--arm", "both", "--trials", "2"),
                 (guidance_dir, "--arm", "both", "--timestamp", TS))
        for argv in cases:
            with self.subTest(argv=argv[1:]):
                self._refused(self._run(*argv), "configuration error:",
                              "is a guidance fixture")


# ---------------------------------------------------------------------------
# 3. trials


class TestIssue66Trials(_HarnessCase):
    """`--trials N`: N trial directories, and an aggregate that counts every
    one of them."""

    SKILL = "trial-skill"

    def setUp(self):
        super().setUp()
        self.fixture_dir = _write_fixture(self.evals / self.SKILL, self.SKILL)
        self.arm_dir = self.results / self.SKILL / TS / "without_skill"

    def _trials(self, n: int, *flags: str):
        return self._run(self.fixture_dir, "--arm", "without_skill",
                         "--trials", str(n), "--timestamp", TS, *flags)

    def _aggregate(self) -> dict:
        return json.loads((self.arm_dir / "summary.json")
                          .read_text(encoding="utf-8"))

    def _report(self) -> str:
        return (self.results / self.SKILL / TS / "report.md").read_text(
            encoding="utf-8")

    def test_three_trials_write_three_trial_dirs_and_an_aggregate(self):
        self._script(
            agents=[{"write": "marker.txt"}, {}, {"write": "marker.txt"}],
            judges=[{"overall": 6.0, "scores": {"Completeness": 5}},
                    {"overall": 9.0, "scores": {"Completeness": 9}},
                    {"overall": 7.5, "scores": {"Completeness": 7}}])
        proc = self._trials(3)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

        prefix = run_eval.TRIAL_DIR_PREFIX
        self.assertEqual(_tree(self.arm_dir), sorted(
            ["summary.json"]
            + [f"{prefix}{k}/summary.json" for k in (1, 2, 3)]
            + [f"{prefix}{k}/transcripts/{leaf}" for k in (1, 2, 3)
               for leaf in ("raw.json", "tool_trace.json")]))
        # Each trial is a whole single-trial summary, labeled with its index.
        for k, overall, marker in ((1, 6.0, True), (2, 9.0, False),
                                   (3, 7.5, True)):
            trial = json.loads((self.arm_dir / f"{prefix}{k}" / "summary.json")
                               .read_text(encoding="utf-8"))
            with self.subTest(trial=k):
                self.assertEqual(trial["trial"], k)
                self.assertNotIn("n", trial)
                self.assertIsNone(trial["error"])
                self.assertEqual(trial["judge"]["overall"], overall)
                self.assertEqual(trial["agent"]["cost_usd"], 0.04)
                self.assertEqual({c["id"]: c["passed"]
                                  for c in trial["objective_checks"]},
                                 {"readme-kept": True,
                                  "marker-written": marker})

        summary = self._aggregate()
        self.assertEqual((summary["n"], summary["errors"], summary["scored"]),
                         (3, 0, 3))
        self.assertEqual(summary["trial_errors"], [])
        self.assertIsNone(summary["error"])
        # The single-trial fields describe no one trial here, so they are
        # null rather than one trial's numbers.
        for field in ("agent", "objective_checks", "judge"):
            self.assertIsNone(summary[field], field)
        self.assertEqual((summary["skill"], summary["arm"],
                          summary["timestamp"]),
                         (self.SKILL, "without_skill", TS))
        self.assertEqual(summary["models_used"], ["fake-agent-model"])
        self.assertEqual(summary["judge_models_used"], ["fake-judge-model"])

        block = summary["aggregate"]
        self.assertEqual(block["judge"]["overall"],
                         {"n": 3, "mean": 7.5, "min": 6.0, "max": 9.0,
                          "sum": 22.5})
        self.assertEqual((block["judge"]["n"], block["judge"]["errors"]),
                         (3, 0))
        completeness = next(d for d in block["judge"]["dimensions"]
                            if d["name"] == "Completeness")
        self.assertEqual(completeness, {"name": "Completeness", "n": 3,
                                        "mean": 7.0, "min": 5, "max": 9,
                                        "sum": 21})
        self.assertEqual(block["objective"], {
            "n": 3, "passed": 5, "total": 6,
            "mean_passed": 5 / 3, "mean_total": 2.0,
            "checks": [
                {"id": "readme-kept", "n": 3, "passed": 3, "pass_rate": 1.0},
                {"id": "marker-written", "n": 3, "passed": 2,
                 "pass_rate": 2 / 3}]})
        cost = block["cost_usd"]
        self.assertEqual((cost["n"], cost["min"], cost["max"]),
                         (3, 0.04, 0.04))
        self.assertAlmostEqual(cost["mean"], 0.04)
        self.assertAlmostEqual(cost["sum"], 0.12)
        # The efficiency figures ride beside the cost, from the same trials.
        # The scripted CLI reports 3 turns and 1234 input tokens each time.
        # Its nonverbose result objects do not establish tool-error counts.
        efficiency = block["efficiency"]
        self.assertEqual(efficiency["num_turns"],
                         {"n": 3, "n_missing": 0, "mean": 3.0, "median": 3,
                          "min": 3, "max": 3, "sum": 9})
        self.assertEqual(efficiency["input_tokens"]["median"], 1234)
        self.assertEqual(efficiency["tool_errors"]["n"], 0)
        self.assertEqual(efficiency["tool_errors"]["n_missing"], 3)
        self.assertIsNone(efficiency["tool_errors"]["max"])

        report = self._report()
        self.assertIn(f"## Fixture: {self.SKILL} (n=3)", report)
        self.assertIn("| num_turns | 3 / 3 |", report)
        self.assertIn("- Trials per arm: 3", report)
        self.assertIn("| without_skill | 3 | 0 | 1.7/2 | "
                      "7.5 (6.0 to 9.0, 3 judged) | 0.0400 / 0.1200 |", report)
        self.assertIn("| marker-written | 2/3 |", report)
        self.assertIn("| readme-kept | 3/3 |", report)
        self.assertNotIn("Errored trials", report)

    def test_every_trial_gets_a_fresh_workspace_and_its_own_calls(self):
        # Trial 1 writes the marker. If trial 2 reused that workspace it
        # would find the marker there and pass the check it must fail.
        self._script(agents=[{"write": "marker.txt"}, {}, {}],
                     judges=[{"overall": 5.0}] * 3)
        proc = self._trials(3)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(len(self._calls("agents")), 3)
        self.assertEqual(len(self._calls("judges")), 3)
        workspaces = [c["cwd"] for c in self._calls("agents")]
        self.assertEqual(len(set(workspaces)), 3, workspaces)
        for workspace in workspaces:
            # Under this test's own scratch dir, and gone once scored.
            self.assertEqual(Path(workspace).parent, self.tmp / "scratch")
            self.assertFalse(Path(workspace).exists(), workspace)
        marker = next(c for c in self._aggregate()["aggregate"]["objective"][
            "checks"] if c["id"] == "marker-written")
        self.assertEqual((marker["passed"], marker["n"]), (1, 3))

    def test_an_errored_trial_is_counted_in_n_and_kept_out_of_the_means(self):
        self._script(
            agents=[{"write": "marker.txt"}, {"mode": "error"}, {}],
            judges=[{"overall": 8.0}, {"overall": 6.0}])
        proc = self._trials(3)
        # A runner-level error, exactly as an errored arm always was.
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("Runner-level error in arm(s): "
                      "without_skill (1 of 3 trials)", proc.stdout)

        summary = self._aggregate()
        self.assertEqual((summary["n"], summary["errors"], summary["scored"]),
                         (3, 1, 2))
        self.assertEqual(len(summary["trial_errors"]), 1)
        entry = summary["trial_errors"][0]
        self.assertEqual((entry["trial"], entry["type"]), (2, "nonzero_exit"))
        self.assertIn("simulated CLI failure", entry["detail"])
        self.assertEqual(summary["error"]["type"], "trial_errors")
        self.assertIn("1 of 3 trials errored (nonzero_exit x1)",
                      summary["error"]["detail"])
        self.assertIn("over the 2 that did not", summary["error"]["detail"])

        block = summary["aggregate"]
        # Over trials 1 and 3 only.
        self.assertEqual(block["judge"]["overall"],
                         {"n": 2, "mean": 7.0, "min": 6.0, "max": 8.0,
                          "sum": 14.0})
        self.assertEqual((block["objective"]["n"], block["objective"]["passed"],
                          block["objective"]["total"]), (2, 3, 4))
        self.assertEqual(block["objective"]["mean_passed"], 1.5)
        self.assertEqual(block["cost_usd"]["n"], 2)
        self.assertAlmostEqual(block["cost_usd"]["sum"], 0.08)
        # The errored trial is still on disk, with its error.
        errored = json.loads(
            (self.arm_dir / f"{run_eval.TRIAL_DIR_PREFIX}2" / "summary.json")
            .read_text(encoding="utf-8"))
        self.assertEqual(errored["error"]["type"], "nonzero_exit")

        report = self._report()
        self.assertIn("| without_skill | 3 | 1 | 1.5/2 | "
                      "7.0 (6.0 to 8.0, 2 judged) | "
                      "0.0400 / 0.0800; 1 unknown |", report)
        self.assertIn("Errored trials are counted in n and excluded from "
                      "score means; known costs from all trials are counted:",
                      report)
        self.assertIn("- without_skill trial 2: nonzero_exit:", report)

    def test_when_every_trial_errors_there_are_no_statistics(self):
        self._script(agents=[{"mode": "error"}, {"mode": "error"}])
        proc = self._trials(2)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("without_skill (2 of 2 trials)", proc.stdout)
        summary = self._aggregate()
        self.assertEqual((summary["n"], summary["errors"], summary["scored"]),
                         (2, 2, 0))
        self.assertEqual([e["trial"] for e in summary["trial_errors"]], [1, 2])
        efficiency = summary["aggregate"].pop("efficiency")
        # No agent result, so no model's tokens (test_issue_model_tokens_effort).
        self.assertEqual(summary["aggregate"].pop("model_tokens"), {})
        self.assertEqual(summary["aggregate"].pop("cross_model")["n"], 0)
        self.assertEqual(summary["aggregate"],
                         {"objective": None, "judge": None, "cost_usd": None,
                          "cost_unknown_trials": 2})
        # No agent call returned, so no efficiency figure exists to average.
        self.assertTrue(all(block["n"] == 0 and block["n_missing"] == 2
                            for block in efficiency.values()))
        self.assertIn("no trial was scored", summary["error"]["detail"])
        self.assertEqual(self._calls("judges"), [])
        self.assertIn("| without_skill | 2 | 2 | - | - | -; 2 unknown |",
                      self._report())

    def test_a_failed_judge_call_is_a_judge_error_not_a_trial_error(self):
        self._script(agents=[{}, {}, {}],
                     judges=[{"overall": 8.0}, {"fail": True},
                             {"overall": 6.0}])
        proc = self._trials(3)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        summary = self._aggregate()
        self.assertEqual((summary["n"], summary["errors"], summary["scored"]),
                         (3, 0, 3))
        self.assertIsNone(summary["error"])
        judge = summary["aggregate"]["judge"]
        self.assertEqual((judge["n"], judge["errors"]), (2, 1))
        self.assertEqual(judge["overall"],
                         {"n": 2, "mean": 7.0, "min": 6.0, "max": 8.0,
                          "sum": 14.0})
        # The objective checks and the cost are over all three.
        self.assertEqual(summary["aggregate"]["objective"]["n"], 3)
        self.assertEqual(summary["aggregate"]["cost_usd"]["n"], 3)
        self.assertIn("7.0 (6.0 to 8.0, 2 judged); 1 judge error(s)",
                      self._report())

    def test_when_every_judge_call_fails_the_judge_cell_says_error(self):
        self._script(agents=[{}, {}], judges=[{"fail": True}, {"fail": True}])
        proc = self._trials(2)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self._aggregate()["aggregate"]["judge"],
                         {"n": 0, "errors": 2, "overall": None,
                          "dimensions": []})
        self.assertIn("| without_skill | 2 | 0 | 1/2 | error | "
                      "0.0400 / 0.0800 |", self._report())

    def test_a_fixture_with_no_checks_has_no_objective_figures(self):
        bare = _write_fixture(self.evals / "bare-skill", "bare-skill",
                              objective_checks=[])
        self._script(agents=[{}, {}])
        proc = self._run(bare, "--arm", "without_skill", "--trials", "2",
                         "--no-judge", "--timestamp", TS)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        summary = self._json("bare-skill", TS, "without_skill", "summary.json")
        self.assertEqual(summary["aggregate"]["objective"],
                         {"n": 2, "passed": 0, "total": 0, "mean_passed": 0.0,
                          "mean_total": 0.0, "checks": []})
        report = (self.results / "bare-skill" / TS / "report.md").read_text(
            encoding="utf-8")
        # Nothing was checked, so no pass count is printed — not "0/0".
        self.assertIn("| without_skill | 2 | 0 | - | - | 0.0400 / 0.0800 |",
                      report)
        self.assertNotIn("| Check", report)

    def test_a_check_id_cannot_break_the_pass_rate_table(self):
        piped = _write_fixture(
            self.evals / "piped-skill", "piped-skill",
            objective_checks=[dict(CHECKS[0], id="kept | or not")])
        self._script(agents=[{}, {}])
        proc = self._run(piped, "--arm", "without_skill", "--trials", "2",
                         "--no-judge", "--timestamp", TS)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        report = (self.results / "piped-skill" / TS / "report.md").read_text(
            encoding="utf-8")
        self.assertIn("| kept \\| or not | 2/2 |\n", report)

    def test_an_arm_with_no_scored_trial_has_no_pass_rates(self):
        # with_skill errors twice; without_skill is scored twice.
        self._script(agents=[{"mode": "error"}] * 2 + [{}] * 2)
        proc = self._run(self.fixture_dir, "--arm", "both", "--trials", "2",
                         "--no-judge", "--timestamp", TS, "--registry",
                         f"acsi-agentskills={self._registry(self.SKILL)}")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("Runner-level error in arm(s): "
                      "with_skill (2 of 2 trials)\n", proc.stdout)
        report = self._report()
        self.assertIn("| with_skill | 2 | 2 | - | - | -; 2 unknown |", report)
        self.assertIn("| readme-kept | - | 2/2 |", report)
        self.assertIn("| marker-written | - | 0/2 |", report)

    def test_without_a_judge_the_aggregate_has_no_judge_block(self):
        self._script(agents=[{}, {}])
        proc = self._trials(2, "--no-judge")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        summary = self._aggregate()
        self.assertIsNone(summary["aggregate"]["judge"])
        self.assertEqual(summary["aggregate"]["objective"]["n"], 2)
        self.assertEqual(self._calls("judges"), [])
        self.assertIn("| without_skill | 2 | 0 | 1/2 | - | "
                      "0.0400 / 0.0800 |", self._report())

    def test_both_arms_are_trialed_and_aggregated_separately(self):
        self._script(agents=[{"write": "marker.txt"}] * 2 + [{}] * 2,
                     judges=[{"overall": 9.0}] * 2 + [{"overall": 4.0}] * 2)
        # `--judge always`: the arms differ, so the default would skip the
        # judge (#371); this test is about aggregating its scores.
        proc = self._run(self.fixture_dir, "--arm", "both", "--trials", "2",
                         "--judge", "always", "--timestamp", TS, "--registry",
                         f"acsi-agentskills={self._registry(self.SKILL)}")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        got = {}
        for arm in ("with_skill", "without_skill"):
            summary = self._json(self.SKILL, TS, arm, "summary.json")
            got[arm] = (summary["n"],
                        summary["aggregate"]["objective"]["passed"],
                        summary["aggregate"]["judge"]["overall"]["mean"])
        self.assertEqual(got, {"with_skill": (2, 4, 9.0),
                               "without_skill": (2, 2, 4.0)})

    def test_trials_of_nested_fixtures_go_under_each_fixture(self):
        skill_dir = self.evals / "multi-skill"
        for name in ("alpha", "beta"):
            _write_fixture(skill_dir / name, "multi-skill")
        self._script(agents=[{}] * 4)
        proc = self._run(skill_dir, "--arm", "without_skill", "--no-judge",
                         "--trials", "2", "--timestamp", TS)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        prefix = run_eval.TRIAL_DIR_PREFIX
        for name in ("alpha", "beta"):
            arm_dir = self.results / "multi-skill" / TS / name / "without_skill"
            self.assertEqual(sorted(p.name for p in arm_dir.iterdir()),
                             ["summary.json", f"{prefix}1", f"{prefix}2"])
            summary = json.loads((arm_dir / "summary.json")
                                 .read_text(encoding="utf-8"))
            self.assertEqual((summary["fixture"], summary["n"]), (name, 2))
            trial = json.loads((arm_dir / f"{prefix}2" / "summary.json")
                               .read_text(encoding="utf-8"))
            self.assertEqual((trial["fixture"], trial["trial"]), (name, 2))
        report = (self.results / "multi-skill" / TS / "report.md").read_text(
            encoding="utf-8")
        self.assertIn("## Fixture: alpha (n=2)", report)
        self.assertIn("## Fixture: beta (n=2)", report)

    def test_a_trial_count_out_of_range_is_refused_before_anything_runs(self):
        self._script(agents=[{}] * 3)
        for value in ("0", "-1", str(run_eval.MAX_TRIALS + 1)):
            with self.subTest(trials=value):
                self._refused(self._trials(int(value)),
                              "configuration error: --trials must be between "
                              f"1 and {run_eval.MAX_TRIALS}, got {value}")
        self._refused(self._run(self.fixture_dir, "--arm", "objective-only",
                                "--trials", "2"),
                      "configuration error: --trials applies to the agent arms")

    def test_the_largest_trial_count_allowed_is_accepted(self):
        # The bound is inclusive: MAX_TRIALS itself runs.
        self._script(agents=[{}] * run_eval.MAX_TRIALS)
        proc = self._trials(run_eval.MAX_TRIALS, "--no-judge")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self._aggregate()["n"], run_eval.MAX_TRIALS)

    def test_a_reused_timestamp_never_writes_over_a_run(self):
        # Three trials, then two under the same --timestamp: written into
        # again, the arm would keep trial-3/ beside an aggregate saying n=2.
        self._script(agents=[{}] * 5)
        first = self._trials(3, "--no-judge")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        before = {rel: (self.results / rel).read_bytes()
                  for rel in _tree(self.results)}
        second = self._trials(2, "--no-judge")
        self.assertEqual(second.returncode, 2, second.stdout + second.stderr)
        self.assertIn(f"configuration error: --timestamp {TS} names a run "
                      "that already holds", second.stdout)
        self.assertIn(str(self.arm_dir), second.stdout)
        self.assertEqual(len(self._calls("agents")), 3)
        self.assertEqual({rel: (self.results / rel).read_bytes()
                          for rel in _tree(self.results)}, before)
        # The other arm of the same run directory is not taken, and runs.
        third = self._run(self.fixture_dir, "--arm", "with_skill", "--no-judge",
                          "--timestamp", TS, "--registry",
                          f"acsi-agentskills={self._registry(self.SKILL)}")
        self.assertEqual(third.returncode, 0, third.stdout + third.stderr)

    def test_a_check_id_that_is_not_a_string_is_still_aggregated(self):
        # YAML hands over any value as an id. `main` scored such a fixture
        # and exited 0; so does the default path here, and so do trials.
        odd = _write_fixture(
            self.evals / "odd-skill", "odd-skill",
            objective_checks=[dict(CHECKS[0], id=["readme", "kept"]),
                              dict(CHECKS[1], id=7)])
        self._script(agents=[{}] * 3)
        single = self._run(odd, "--arm", "without_skill", "--no-judge",
                           "--timestamp", TS)
        self.assertEqual(single.returncode, 0, single.stdout + single.stderr)
        self.assertNotIn("Traceback", single.stderr)
        self.assertEqual(
            [c["id"] for c in self._json("odd-skill", TS, "without_skill",
                                         "summary.json")["objective_checks"]],
            [["readme", "kept"], 7])
        later = "20260717T070000Z"
        trials = self._run(odd, "--arm", "without_skill", "--no-judge",
                           "--trials", "2", "--timestamp", later)
        self.assertEqual(trials.returncode, 0, trials.stdout + trials.stderr)
        self.assertEqual(
            self._json("odd-skill", later, "without_skill",
                       "summary.json")["aggregate"]["objective"]["checks"],
            [{"id": ["readme", "kept"], "n": 2, "passed": 2, "pass_rate": 1.0},
             {"id": 7, "n": 2, "passed": 0, "pass_rate": 0.0}])

    def test_a_timestamp_that_is_not_one_is_refused(self):
        self._script(agents=[{}])
        for value in ("2026", "20260716T070000", "20260716T070000z",
                      "20261301T000000Z", "20260716T070000Z/x", "../escape",
                      ""):
            with self.subTest(timestamp=value):
                self._refused(
                    self._run(self.fixture_dir, "--arm", "without_skill",
                              "--timestamp", value),
                    "configuration error: --timestamp", "YYYYMMDDTHHMMSSZ")


# ---------------------------------------------------------------------------
# 4. the statistics, for shapes the entry point cannot easily be made to write


class TestIssue66Timeouts(_HarnessCase):
    """The real trial loop and subprocess.run timeout handler, without a child
    process or any elapsed-time dependency."""

    def test_each_trial_has_a_fresh_timeout_and_continues_after_expiry(self):
        for label, override, fixture_timeout, expected in (
                ("default", None, None, 600),
                ("override", 17, 23, 17),
                ("fixture", None, 23, 23)):
            with self.subTest(timeout=label):
                case_dir = self.tmp / label
                case_dir.mkdir()
                fixture = {"skill": "timeout-skill", "prompt": "stand-in",
                           "judge_rubric": "stand-in"}
                if fixture_timeout is not None:
                    fixture["timeout_s"] = fixture_timeout
                args = argparse.Namespace(
                    trials=3, timeout=override, no_judge=False,
                    results_dir=case_dir / "results")
                children, workspaces = [], []
                testcase = self

                class InstantChild:
                    def __init__(self, cmd, **kwargs):
                        self.args = cmd
                        self.workspace = kwargs["cwd"]
                        self.index = len(children)
                        self.returncode = None
                        self.killed = self.reaped = False
                        self.timeouts = []
                        children.append(self)

                    def __enter__(self):
                        return self

                    def __exit__(self, *_exc):
                        self.wait()

                    def communicate(self, input=None, timeout=None):
                        if self.killed:
                            # Windows drains output after kill; POSIX waits.
                            return "", ""
                        testcase.assertEqual(
                            timeout, expected,
                            "each trial must receive its full timeout")
                        self.timeouts.append(timeout)
                        if self.index == 1:
                            raise subprocess.TimeoutExpired(self.args, timeout)
                        self.returncode = 0
                        return json.dumps({"result": "stand-in transcript",
                                           "total_cost_usd": 0.04}), ""

                    def kill(self):
                        self.killed = True

                    def wait(self, timeout=None):
                        self.reaped = True
                        self.returncode = -9 if self.killed else 0
                        return self.returncode

                    def poll(self):
                        return self.returncode

                def materialize(_seed, _fixture):
                    workspace = case_dir / f"workspace-{len(workspaces) + 1}"
                    workspace.mkdir()
                    workspaces.append(workspace)
                    return workspace

                # Keep real _run_arm, run_agent, subprocess.run and cleanup.
                # Popen alone supplies the child; no CLI can be launched.
                with (mock.patch.object(run_eval, "materialize_workspace",
                                        side_effect=materialize),
                      mock.patch.object(run_eval, "assert_stand_ins_on_path"),
                      mock.patch.object(run_eval, "agent_env",
                                        return_value=_child_env(case_dir)),
                      mock.patch.dict(os.environ,
                                      {"CLAUDE_BIN": str(FAKE_CLAUDE)}),
                      mock.patch.object(subprocess, "Popen", InstantChild),
                      mock.patch.object(run_eval.objective, "run_checks",
                                        return_value=[{"id": "only",
                                                       "passed": True,
                                                       "detail": ""}]) as checks,
                      mock.patch.object(run_eval, "_build_judge_diff",
                                        return_value=""),
                      mock.patch.object(run_eval.judge, "score",
                                        return_value={"overall": 8.0,
                                                      "dimensions": []}) as score):
                    result = run_eval._run_arm_trials(
                        "without_skill",
                        {"fixture": fixture, "seed": case_dir / "seed",
                         "name": None}, {}, args, TS, (None, None, None))

                self.assertEqual(len(children), 3)
                self.assertEqual([c.timeouts for c in children],
                                 [[expected], [expected], [expected]])
                self.assertTrue(children[1].killed)
                self.assertTrue(children[1].reaped)
                self.assertFalse(children[0].killed)
                self.assertFalse(children[2].killed)
                self.assertEqual([c.workspace for c in children], workspaces)
                self.assertEqual(len(set(workspaces)), 3)
                self.assertTrue(all(not w.exists() for w in workspaces))
                self.assertEqual(checks.call_count, 2)
                self.assertEqual([call.args[1] for call in checks.call_args_list],
                                 [str(workspaces[0]), str(workspaces[2])])
                self.assertEqual(score.call_count, 2)

                arm_dir = args.results_dir / fixture["skill"] / TS / "without_skill"
                trials = [json.loads((arm_dir / f"trial-{k}" / "summary.json")
                                     .read_text(encoding="utf-8"))
                          for k in (1, 2, 3)]
                self.assertEqual([t["trial"] for t in trials], [1, 2, 3])
                self.assertIsNone(trials[0]["error"])
                self.assertEqual(trials[1]["error"], {
                    "type": "timeout",
                    "detail": f"agent timed out after {expected}s"})
                self.assertIsNone(trials[1]["agent"])
                self.assertIsNone(trials[1]["objective_checks"])
                self.assertIsNone(trials[1]["judge"])
                self.assertIsNone(trials[2]["error"])
                summary = json.loads((arm_dir / "summary.json")
                                     .read_text(encoding="utf-8"))
                stats = result["stats"]
                self.assertEqual((stats["n"], stats["errors"], stats["scored"]),
                                 (3, 1, 2))
                self.assertEqual(summary["aggregate"], stats["aggregate"])
                self.assertEqual(stats["aggregate"]["cost_unknown_trials"], 1)
                self.assertEqual(stats["aggregate"]["cost_usd"]["n"], 2)
                self.assertEqual(stats["aggregate"]["objective"]["n"], 2)
                self.assertEqual(stats["aggregate"]["judge"]["n"], 2)


class TestIssue66Statistics(unittest.TestCase):
    """`run_eval.aggregate_trials` — the function `--trials` aggregates with —
    on judge and cost values a CLI could report but test/fake-claude does
    not."""

    @staticmethod
    def _trial(overall=None, cost=0.5, passed=True, dims=None, error=None,
               junk=()):
        if error:
            return {"error": error, "agent": None, "objective_checks": None,
                    "judge": None}
        judge = None
        if overall is not None:
            judge = {"overall": overall,
                     "dimensions": [
                         {"name": name, "score": score, "rationale": ""}
                         for name, score in (dims or {}).items()] + list(junk)}
        return {"error": None, "agent": {"cost_usd": cost},
                "objective_checks": [{"id": "only", "passed": passed,
                                      "detail": ""}],
                "judge": judge}

    def test_one_trial_is_its_own_mean_min_and_max(self):
        stats = run_eval.aggregate_trials([self._trial(overall=7.5, cost=0.25)])
        self.assertEqual((stats["n"], stats["errors"], stats["scored"]),
                         (1, 0, 1))
        self.assertEqual(stats["aggregate"]["judge"]["overall"],
                         {"n": 1, "mean": 7.5, "min": 7.5, "max": 7.5,
                          "sum": 7.5})
        self.assertEqual(stats["aggregate"]["cost_usd"],
                         {"n": 1, "mean": 0.25, "min": 0.25, "max": 0.25,
                          "sum": 0.25})

    def test_values_that_are_not_finite_numbers_are_left_out_and_counted(self):
        # NaN, infinity, a bool, a string, and an int with no float: none is
        # a score, and each is a judge result without a usable overall.
        junk = [float("nan"), float("inf"), True, "8", 10 ** 400]
        trials = [self._trial(overall=6.0)] + [self._trial(overall=value)
                                               for value in junk]
        trials.append(self._trial(overall=8.0, cost=float("nan")))
        stats = run_eval.aggregate_trials(trials)
        judge = stats["aggregate"]["judge"]
        self.assertEqual((judge["n"], judge["errors"]), (2, len(junk)))
        self.assertEqual(judge["overall"],
                         {"n": 2, "mean": 7.0, "min": 6.0, "max": 8.0,
                          "sum": 14.0})
        # The NaN cost is left out of the cost figures; its trial still
        # counts for the objective checks.
        self.assertEqual(stats["aggregate"]["cost_usd"]["n"], len(trials) - 1)
        self.assertEqual(stats["aggregate"]["objective"]["n"], len(trials))

    def test_dimensions_are_matched_trimmed_and_casefolded(self):
        stats = run_eval.aggregate_trials([
            self._trial(overall=5.0, dims={"Completeness": 4, "Tone": 9}),
            # ...and a score that is not a number, and an entry that is not
            # a dimension at all, are left out rather than averaged.
            self._trial(overall=5.0, dims={" completeness ": 8,
                                           "Tone": "high"},
                        junk=["not a dimension", None]),
        ])
        self.assertEqual(stats["aggregate"]["judge"]["dimensions"], [
            {"name": "Completeness", "n": 2, "mean": 6.0, "min": 4, "max": 8,
             "sum": 12},
            {"name": "Tone", "n": 1, "mean": 9.0, "min": 9, "max": 9,
             "sum": 9}])

    def test_an_id_used_for_two_checks_is_counted_each_time(self):
        # One id on two checks of one fixture: its `n` is how many times it
        # was scored, which is then twice per trial.
        twice = {"error": None, "agent": {"cost_usd": 0.5}, "judge": None,
                 "objective_checks": [
                     {"id": "same", "passed": True, "detail": ""},
                     {"id": "same", "passed": False, "detail": ""}]}
        stats = run_eval.aggregate_trials([twice] * 3)
        self.assertEqual(stats["aggregate"]["objective"], {
            "n": 3, "passed": 3, "total": 6, "mean_passed": 1.0,
            "mean_total": 2.0,
            "checks": [{"id": "same", "n": 6, "passed": 3,
                        "pass_rate": 0.5}]})

    def test_an_errored_trial_never_leaves_n(self):
        error = {"type": "timeout", "detail": "agent timed out after 600s"}
        # The third errored after its agent call was paid for (the shape an
        # `invalid_fixture` trial has): still errored, its cost still counted.
        paid = dict(self._trial(error=error), agent={"cost_usd": 9.0})
        stats = run_eval.aggregate_trials([
            self._trial(error=error), self._trial(overall=4.0, passed=False),
            paid])
        self.assertEqual((stats["n"], stats["errors"], stats["scored"]),
                         (3, 2, 1))
        self.assertEqual(stats["aggregate"]["cost_usd"],
                         {"n": 2, "mean": 4.75, "min": 0.5, "max": 9.0,
                          "sum": 9.5})
        self.assertEqual(stats["aggregate"]["cost_unknown_trials"], 1)
        self.assertEqual([e["trial"] for e in stats["trial_errors"]], [1, 3])
        self.assertEqual(stats["aggregate"]["objective"]["checks"],
                         [{"id": "only", "n": 1, "passed": 0,
                           "pass_rate": 0.0}])
        self.assertEqual(stats["aggregate"]["objective"]["n"], 1)
        self.assertEqual(stats["aggregate"]["judge"]["overall"]["n"], 1)
        self.assertEqual(run_eval._trials_error(stats)["detail"],
                         "2 of 3 trials errored (timeout x2); score means are "
                         "over the 1 that did not")

    def test_known_costs_count_when_every_trial_errors(self):
        error = {"type": "invalid_fixture", "detail": "unusable check"}
        trials = [dict(self._trial(error=error), agent={"cost_usd": cost})
                  for cost in (0.0, 9.0)] + [self._trial(error=error)]
        stats = run_eval.aggregate_trials(trials)
        self.assertEqual((stats["n"], stats["errors"], stats["scored"]),
                         (3, 3, 0))
        stats["aggregate"].pop("efficiency")  # pinned in test_issue_efficiency_metrics
        # Pinned in test_issue_model_tokens_effort.
        stats["aggregate"].pop("model_tokens")
        stats["aggregate"].pop("cross_model")
        self.assertEqual(stats["aggregate"], {
            "objective": None, "judge": None,
            "cost_usd": {"n": 2, "mean": 4.5, "min": 0.0, "max": 9.0,
                         "sum": 9.0},
            "cost_unknown_trials": 1})

    def test_unknown_costs_are_counted_across_scored_and_errored_trials(self):
        invalid_agents = [None, [], "invalid", True, 42, {},
                          *[{"cost_usd": value} for value in (
                              None, False, True, "0.5", float("nan"),
                              float("inf"), float("-inf"), 10 ** 400)]]
        trials = []
        for error in (None, {"type": "timeout", "detail": "no result"}):
            missing = self._trial(overall=4.0)
            missing["error"] = error
            del missing["agent"]
            trials.append(missing)
            for agent in invalid_agents:
                trial = self._trial(overall=4.0)
                trial.update(error=error, agent=agent)
                trials.append(trial)
        stats = run_eval.aggregate_trials(trials)
        self.assertEqual((stats["n"], stats["errors"], stats["scored"]),
                         (len(trials), len(trials) // 2, len(trials) // 2))
        self.assertIsNone(stats["aggregate"]["cost_usd"])
        self.assertEqual(stats["aggregate"]["cost_unknown_trials"], len(trials))
        self.assertEqual(stats["aggregate"]["objective"]["n"], len(trials) // 2)
        self.assertEqual(stats["aggregate"]["judge"]["n"], len(trials) // 2)


# ---------------------------------------------------------------------------
# 5. the badge


class TestIssue66Badge(_HarnessCase):
    """scripts/make_badge.py over the published single-trial shape, the
    aggregate shape, and nested fixtures."""

    SKILL = "workflow-path-audit"

    @staticmethod
    def _single(passed: int, total: int, judge=None, **extra) -> dict:
        """A single-trial arm summary. With no `extra` it is the shape every
        published run has; `n=1` makes it the one `--trials 1` writes now."""
        return {"error": None,
                "objective_checks": [{"id": f"c{i}", "passed": i < passed,
                                      "detail": ""} for i in range(total)],
                "judge": {"overall": judge} if judge is not None else None,
                **extra}

    @staticmethod
    def _trialed(trials: list[dict]) -> dict:
        """The aggregate arm summary `--trials N` writes for these trial
        summaries — built by the harness's own functions, so this test and
        the writer cannot disagree about the shape."""
        stats = run_eval.aggregate_trials(trials)
        return {"error": run_eval._trials_error(stats), "agent": None,
                "objective_checks": None, "judge": None, **stats}

    def _write(self, ts: str, with_summary: dict, without_summary: dict,
               fixture: str | None = None) -> None:
        unit = self.results / self.SKILL / ts
        if fixture:
            unit = unit / fixture
        for arm, summary in (("with_skill", with_summary),
                             ("without_skill", without_summary)):
            (unit / arm).mkdir(parents=True)
            (unit / arm / "summary.json").write_text(json.dumps(summary),
                                                     encoding="utf-8")

    def _badge(self, *flags: str) -> dict:
        """`python3 scripts/make_badge.py <skill> <flags>`: its output file,
        parsed, after checking the file is in the published format."""
        out = self.tmp / "badge.json"
        cmd = [sys.executable, str(SCRIPTS_DIR / "make_badge.py"), self.SKILL,
               "--results-dir", str(self.results), "--out", str(out), *flags]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              env=_child_env(self.tmp), cwd=str(self.tmp),
                              timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        text = out.read_text(encoding="utf-8")
        badge = json.loads(text)
        self.assertEqual(text, json.dumps(badge, indent=2, sort_keys=True) + "\n")
        return badge

    # -- the published shape: the message `main` prints -------------------

    def test_window_1_on_a_published_run_is_the_message_main_prints(self):
        # The expected badges below are what `main`'s make_badge.py (ed135aa)
        # prints for these same trees.
        self._write("20260709T070000Z", self._single(3, 5, 6.0),
                    self._single(4, 5, 7.0))
        self._write(TS, self._single(5, 5, 8.5), self._single(3, 5, 4.0))
        self.assertEqual(self._badge("--window", "1"), {
            "schemaVersion": 1, "label": f"skill eval: {self.SKILL}",
            "message": f"with 5/5 vs without 3/5 · {DATE}", "color": "green"})
        # ...and the default window, which is what the publish job runs.
        self.assertEqual(self._badge(), {
            "schemaVersion": 1, "label": f"skill eval: {self.SKILL}",
            "message": f"with 4/5 vs without 3.5/5 · n=2 · {DATE}",
            "color": "green"})

    def test_a_run_carrying_n_1_reads_exactly_as_a_published_one(self):
        # What the harness writes at `--trials 1` from now on, beside what is
        # already published: same message as two runs without `n`.
        self._write("20260709T070000Z", self._single(3, 5, 6.0),
                    self._single(4, 5, 7.0))
        self._write(TS, self._single(5, 5, 8.5, n=1),
                    self._single(3, 5, 4.0, n=1))
        self.assertEqual(self._badge("--window", "1")["message"],
                         f"with 5/5 vs without 3/5 · {DATE}")
        self.assertEqual(self._badge()["message"],
                         f"with 4/5 vs without 3.5/5 · n=2 · {DATE}")

    # -- trials and fixtures ----------------------------------------------

    def test_n_is_trials_times_runs(self):
        for ts in ("20260709T070000Z", TS):
            self._write(ts,
                        self._trialed([self._single(5, 5, 9.0),
                                        self._single(4, 5, 8.0),
                                        self._single(5, 5, 7.0)]),
                        self._trialed([self._single(3, 5, 5.0)] * 3))
        badge = self._badge()
        # with: (5+4+5)*2 / 6 trials = 4.67 -> 4.7; without: 3.
        self.assertEqual(badge["message"],
                         f"with 4.7/5 vs without 3/5 · n=6 · {DATE}")
        self.assertEqual(badge["color"], "green")
        self.assertEqual(self._badge("--window", "1")["message"],
                         f"with 4.7/5 vs without 3/5 · n=3 · {DATE}")

    def test_the_window_averages_over_every_fixture_of_the_skill(self):
        # One run of two nested fixtures at two trials each, and an older
        # single-trial flat run: 2 + 2 + 1 trials per arm.
        self._write(TS, self._trialed([self._single(4, 4)] * 2),
                    self._trialed([self._single(2, 4)] * 2), fixture="alpha")
        self._write(TS, self._trialed([self._single(1, 2)] * 2),
                    self._trialed([self._single(1, 2)] * 2), fixture="beta")
        self._write("20260709T070000Z", self._single(3, 4),
                    self._single(0, 4))
        # with: (8 + 2 + 3) / 5 = 2.6 of (8 + 4 + 4) / 5 = 3.2
        # without: (4 + 2 + 0) / 5 = 1.2 of 3.2
        self.assertEqual(self._badge()["message"],
                         f"with 2.6/3.2 vs without 1.2/3.2 · n=5 · {DATE}")
        # The newest run alone: both of its fixtures, none of the older run.
        self.assertEqual(self._badge("--window", "1")["message"],
                         f"with 2.5/3 vs without 1.5/3 · n=4 · {DATE}")

    def test_the_judge_mean_is_over_every_judged_trial(self):
        # Objective tied; the judge decides between yellow and red. The
        # with-arm's judge mean is (9 + 3 + 3) / 3 = 5, below without's 6.
        self._write(TS, self._trialed([self._single(4, 5, 9.0),
                                        self._single(4, 5, 3.0),
                                        self._single(4, 5, 3.0)]),
                    self._trialed([self._single(4, 5, 6.0)] * 3))
        self.assertEqual(self._badge()["color"], "red")
        # A newer run in which one with-arm trial has no judge result: the
        # mean is over the two that do, (8 + 8) / 2 = 8, above without's 6 —
        # not (8 + 8) / 3, which would be below it.
        newer = "20260723T070000Z"
        self._write(newer, self._trialed([self._single(4, 5, 8.0),
                                           self._single(4, 5),
                                           self._single(4, 5, 8.0)]),
                    self._trialed([self._single(4, 5, 6.0)] * 3))
        self.assertEqual(self._badge("--window", "1")["color"], "yellow")

    def test_an_aggregate_with_an_unusable_judge_block_still_counts(self):
        # The judge can only demote. A judge block the badge cannot read is
        # "no judge", and the objective comparison stands.
        without = self._trialed([self._single(3, 5, 9.0)] * 2)
        # -inf and 10**400 are both JSON a file can carry (`-Infinity`, and
        # an integer with no float): neither is a sum to divide.
        for index, (field, value) in enumerate(
                (("sum", "high"), ("sum", None), ("sum", True),
                 ("sum", float("-inf")), ("sum", 10 ** 400), ("n", None),
                 ("n", 0), ("n", 1.5))):
            with_arm = self._trialed([self._single(5, 5, 1.0)] * 2)
            with_arm["aggregate"]["judge"]["overall"][field] = value
            ts = f"202607{10 + index}T070000Z"
            with self.subTest(field=field, value=str(value)[:12]):
                self._write(ts, with_arm, without)
                badge = self._badge("--window", "1")
                self.assertEqual(
                    badge["message"],
                    f"with 5/5 vs without 3/5 · n=2 · 2026-07-{10 + index}")
                # With its judge readable (1.0 against 9.0) this pair is
                # yellow; unreadable, the objective win stands.
                self.assertEqual(badge["color"], "green")
        self._write("20260720T070000Z",
                    self._trialed([self._single(5, 5, 1.0)] * 2), without)
        self.assertEqual(self._badge("--window", "1")["color"], "yellow")

    def test_equal_pass_counts_are_a_tie_whatever_the_trial_order(self):
        # 7 of 9 on both sides, reached through different trials: a tie on
        # the objective checks, so yellow, in whatever order the passes came.
        self._write(TS, self._trialed([self._single(2, 3), self._single(2, 3),
                                        self._single(3, 3)]),
                    self._trialed([self._single(3, 3), self._single(2, 3),
                                    self._single(2, 3)]))
        badge = self._badge()
        self.assertEqual(badge["color"], "yellow")
        self.assertEqual(badge["message"],
                         f"with 2.3/3 vs without 2.3/3 · n=3 · {DATE}")

    def test_a_pair_with_an_errored_trial_is_dropped_not_averaged(self):
        error = {"type": "timeout", "detail": "agent timed out after 600s"}
        errored = self._trialed([self._single(5, 5), self._single(5, 5),
                                  {"error": error, "agent": None,
                                   "objective_checks": None, "judge": None}])
        self._write(TS, errored, self._trialed([self._single(0, 5)] * 3))
        self.assertEqual(self._badge(), {
            "schemaVersion": 1, "label": f"skill eval: {self.SKILL}",
            "message": f"no data · {DATE}", "color": "lightgrey"})
        # An older clean run is what the badge then reports, dated as itself.
        self._write("20260709T070000Z", self._single(4, 5),
                    self._single(4, 5))
        self.assertEqual(self._badge()["message"],
                         "with 4/5 vs without 4/5 · 2026-07-09")

    def test_arms_with_different_trial_counts_are_not_a_pair(self):
        self._write(TS, self._trialed([self._single(5, 5)] * 3),
                    self._trialed([self._single(0, 5)] * 2))
        self.assertEqual(self._badge()["message"], f"no data · {DATE}")

    def test_a_malformed_aggregate_reads_as_missing(self):
        good = self._trialed([self._single(5, 5)] * 2)

        def broken(**changes):
            doc = json.loads(json.dumps(good))
            for path, value in changes.items():
                node = doc
                *parents, leaf = path.split("__")
                for parent in parents:
                    node = node[parent]
                node[leaf] = value
            return doc

        cases = {
            "passed above total": broken(aggregate__objective__passed=11),
            "no checks": broken(aggregate__objective__total=0,
                                aggregate__objective__passed=0),
            "a float count": broken(aggregate__objective__passed=10.0),
            "a negative count": broken(aggregate__objective__passed=-1),
            "objective n disagrees": broken(aggregate__objective__n=3),
            "n is zero": broken(n=0, aggregate__objective__n=0),
            "no aggregate": broken(aggregate=None),
            "errors without an error": broken(errors=1),
            "an error without errors": broken(
                error={"type": "trial_errors", "detail": "hand-edited"}),
            "a check list beside n=2": dict(self._single(5, 5), n=2),
            "a check list beside n=true": dict(self._single(5, 5), n=True),
        }
        # The same malformed summary on BOTH arms, so that what drops the
        # pair is the summary's own shape and not a mismatch between arms.
        for index, (label, summary) in enumerate(cases.items()):
            ts = f"202607{10 + index}T070000Z"
            with self.subTest(case=label):
                self._write(ts, summary, summary)
                self.assertEqual(self._badge("--window", "1"), {
                    "schemaVersion": 1, "label": f"skill eval: {self.SKILL}",
                    "message": f"no data · 2026-07-{10 + index}",
                    "color": "lightgrey"})
        # The control: the summary those were all made from is usable.
        self._write("20260725T070000Z", good, good)
        self.assertEqual(self._badge("--window", "1")["message"],
                         "with 5/5 vs without 5/5 · n=2 · 2026-07-25")

    # -- writer to reader --------------------------------------------------

    def test_the_badge_reads_what_trials_write(self):
        # run_eval.py --trials 2 writes it; make_badge.py reads it.
        fixture = _write_fixture(self.evals / self.SKILL, self.SKILL)
        self._script(agents=[{"write": "marker.txt"}] * 2
                     + [{"write": "marker.txt"}, {}],
                     judges=[{"overall": 8.0}] * 4)
        proc = self._run(fixture, "--arm", "both", "--trials", "2",
                         "--timestamp", TS, "--registry",
                         f"acsi-agentskills={self._registry(self.SKILL)}")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self._badge(), {
            "schemaVersion": 1, "label": f"skill eval: {self.SKILL}",
            "message": f"with 2/2 vs without 1.5/2 · n=2 · {DATE}",
            "color": "green"})

    def test_the_badge_reads_what_a_single_trial_run_writes(self):
        fixture = _write_fixture(self.evals / self.SKILL, self.SKILL)
        self._script(agents=[{"write": "marker.txt"}, {}],
                     judges=[{"overall": 8.0}] * 2)
        proc = self._run(fixture, "--arm", "both", "--timestamp", TS,
                         "--registry",
                         f"acsi-agentskills={self._registry(self.SKILL)}")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self._badge()["message"],
                         f"with 2/2 vs without 1/2 · {DATE}")


if __name__ == "__main__":
    unittest.main()
