#!/usr/bin/env python3
"""scripts/local_eval.py — the local-exhibit runner (ADR 0002, decision 4).

Every test drives the production entry point, `python3 scripts/local_eval.py`,
as a subprocess, against a CLAUDE_BIN that is a per-test DISPATCHER written
into the test's own mkdtemp: it logs each invocation and hands it to the
repo's fakes — `test/fake-claude-init` for the stream-json init probe,
`test/fake-claude` for `--version` and the agent arms — and answers the judge
itself with scores that differ per call, so a mean is distinguishable from a
min. No real `claude` is run, no credential is read, and the environment each
run gets is built from nothing (HOME, TMPDIR and every path under the test's
own mkdtemp).

Discovered and run by test/run_tests.py; also runnable on its own with
`python3 test/issues/test_issue_local_eval.py`.
"""

from __future__ import annotations

import ast
import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LOCAL_EVAL = REPO_ROOT / "scripts" / "local_eval.py"
FAKE_CLAUDE = REPO_ROOT / "test" / "fake-claude"
FAKE_CLAUDE_INIT = REPO_ROOT / "test" / "fake-claude-init"
# These tests drive a FLAT fixture (#66). workflow-path-audit's fixtures are
# nested, so setUpModule stages a copy of one of them at
# `<temp>/evals/workflow-path-audit/`, whose parent is not named after the
# skill and so reads as flat; EVAL_DIR names that copy once it exists.
#: The invented test-only fixtures (the kit ships none, its DECISIONS X9),
#: laid out as a checkout's evals/ (test/example_evals.py).
FIXTURE_EVALS = REPO_ROOT / "test" / "fixtures" / "evals"
FLAT_SOURCE = FIXTURE_EVALS / "workflow-path-audit" / "required-check-routing"
EVAL_DIR = FLAT_SOURCE
GUIDANCE_FIXTURE = REPO_ROOT / "evals" / "guidance" / "_delivery"
SKILL = "workflow-path-audit"
EXHIBIT = "local — not badge input"

HAVE_GIT = shutil.which("git") is not None

DISPATCHER = '''#!{python}
import json, os, sys
LOG = {log!r}
STATE = {state!r}
FAIL_AGENT_CALL = {fail!r}
PLANT = {plant!r}
argv = sys.argv[1:]
if "--version" in argv:
    role = "version"
elif "stream-json" in argv:
    role = "probe"
elif "--setting-sources" in argv[:-1] and argv[argv.index("--setting-sources") + 1]:
    role = "agent"  # the judge passes an EMPTY setting source
else:
    role = "judge"
path = os.path.join(STATE, role)
n = int(open(path).read()) + 1 if os.path.exists(path) else 1
with open(path, "w") as f:
    f.write(str(n))
record = {{"role": role, "n": n, "argv": argv, "cwd": os.getcwd(),
          "env_names": sorted(os.environ),
          "claude_bin": os.environ.get("CLAUDE_BIN"),
          "path_head": os.environ.get("PATH", "").split(os.pathsep)[0],
          "secret_env": sorted(k for k in os.environ
                               if k.startswith("ANTHROPIC_")
                               or k == "CLAUDE_CODE_OAUTH_TOKEN")}}
if role == "probe":
    record["cwd_entries"] = sorted(os.listdir("."))
    record["env"] = {{k: os.environ[k] for k in ("ANTHROPIC_BASE_URL", "HOME")
                     if k in os.environ}}
with open(LOG, "a") as f:
    f.write(json.dumps(record) + "\\n")
if role == "agent" and PLANT:
    os.makedirs(os.path.dirname(PLANT[0]), exist_ok=True)
    with open(PLANT[0], "w") as f:
        f.write(PLANT[1])
if role == "agent" and n == FAIL_AGENT_CALL:
    sys.stderr.write("simulated agent failure\\n")
    sys.exit(3)
if role == "judge":
    sys.stdin.read()
    base = (6, 8, 9)[(n - 1) % 3]
    dims = [{{"name": "Completeness", "score": base, "rationale": "r"}},
            {{"name": "Salience", "score": base - 1, "rationale": "r"}},
            {{"name": "Restraint", "score": 10 - base, "rationale": "r"}}]
    print(json.dumps({{"result": json.dumps({{"dimensions": dims, "overall": 0}}),
                      "modelUsage": {{"fake-judge-model": {{}}}}}}))
    sys.exit(0)
target = {fake_init!r} if role == "probe" else {fake!r}
os.execv(sys.executable, [sys.executable, target, *argv])
'''


SHIM = '''#!{python}
import json, os, sys
with open({log!r}, "a") as f:
    f.write(json.dumps({{"tool": {tool!r}, "argv": sys.argv[1:]}}) + "\\n")
real = {real!r}
if real:
    os.execv(real, [real, *sys.argv[1:]])
sys.stderr.write("shim: {tool} is not available in this test\\n")
sys.exit(97)
'''


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), "-c", "user.name=t",
         "-c", "user.email=t@example.com", *args],
        capture_output=True, text=True, check=True)


def _plant_skill(skills_dir: Path, name: str) -> None:
    (skills_dir / name).mkdir(parents=True)
    (skills_dir / name / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test stand-in.\n---\n",
        encoding="utf-8")


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from arm_test_env import arm_test_environment, install_arm_test_environment  # noqa: E402


@install_arm_test_environment
def setUpModule() -> None:
    global EVAL_DIR
    staging = Path(tempfile.mkdtemp(prefix="local-eval-flat-",
                                    dir=TestLocalEval._temp_base())).resolve()
    unittest.addModuleCleanup(shutil.rmtree, staging, ignore_errors=True)
    EVAL_DIR = staging / "evals" / SKILL
    shutil.copytree(FLAT_SOURCE, EVAL_DIR)


def tearDownModule() -> None:
    unittest.doModuleCleanups()


@unittest.skipUnless(HAVE_GIT, "git is required")
class TestLocalEval(unittest.TestCase):

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(
            prefix="local-eval-test-", dir=self._temp_base())).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.home = self.root / "home"
        self.home.mkdir()
        (self.root / "tmp").mkdir()
        self.state = self.root / "state"
        self.state.mkdir()
        self.log = self.root / "calls.jsonl"
        self.out = self.root / "out"

    @staticmethod
    def _temp_base():
        """A temp parent no other process writes to, with no `.git` entry in
        it or above it: the runner refuses a results dir under one, and a
        shared temp dir can grow one at any moment (an empty `.git` in /tmp
        is enough). /dev/shm first, when it is writable and allows exec."""
        for base in ("/dev/shm", tempfile.gettempdir()):
            path = Path(base).resolve()
            try:
                usable = (path.is_dir() and os.access(path, os.W_OK | os.X_OK)
                          and not os.statvfs(path).f_flag & os.ST_NOEXEC)
            except OSError:
                usable = False
            if usable and not any((p / ".git").exists()
                                  for p in (path, *path.parents)):
                return str(path)
        raise unittest.SkipTest("no temp directory outside a git repository")

    # -- helpers ---------------------------------------------------------

    def _dispatcher(self, fail_agent_call=None, plant=None) -> Path:
        path = self.root / "claude-dispatch"
        path.write_text(DISPATCHER.format(
            python=sys.executable, log=str(self.log), state=str(self.state),
            fail=fail_agent_call, plant=plant, fake=str(FAKE_CLAUDE),
            fake_init=str(FAKE_CLAUDE_INIT)), encoding="utf-8")
        path.chmod(0o755)
        return path

    def _registry(self, skill: str = SKILL) -> Path:
        registry = self.root / "registry"
        _plant_skill(registry / "plugins" / "a-bundle" / "skills", skill)
        _git(registry, "init", "-q")
        _git(registry, "add", "-A")
        _git(registry, "commit", "-q", "-m", "registry")
        return registry

    def _env(self, dispatcher: Path, **extra) -> dict:
        path = [str(Path(sys.executable).parent),
                str(Path(shutil.which("git")).parent), "/usr/bin", "/bin"]
        env = {**arm_test_environment(),
               "PATH": os.pathsep.join(dict.fromkeys(path)),
               "HOME": str(self.home), "TMPDIR": str(self.root / "tmp"),
               "LANG": "C.UTF-8", "CLAUDE_BIN": str(dispatcher)}
        env.update(extra)
        return env

    def _run(self, *args, fail_agent_call=None, env_extra=None,
             results_dir=None, plant=None):
        env = self._env(self._dispatcher(fail_agent_call, plant),
                        **(env_extra or {}))
        out = self.out if results_dir is None else results_dir
        for attempt in (1, 2):
            proc = subprocess.run(
                [sys.executable, str(LOCAL_EVAL), *args, "--results-dir", str(out)],
                capture_output=True, text=True, env=env, cwd=str(self.root),
                timeout=600)
            # A `.git` that appeared in a directory ABOVE this test's own tree
            # (another process, a shared /tmp) is not what a test is about:
            # run once more. A `.git` the test planted is never retried.
            hit = re.search(r"resolves inside (\S+), which holds a git repository",
                            proc.stderr)
            foreign = bool(hit) and Path(hit.group(1)) in self.root.parents
            if attempt == 2 or not (proc.returncode == 2 and foreign):
                return proc
            self.log.unlink(missing_ok=True)

    def _calls(self) -> list:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in
                self.log.read_text(encoding="utf-8").splitlines()]

    def _json(self, name: str) -> dict:
        return json.loads((self.out / name).read_text(encoding="utf-8"))

    def _summaries(self, arm: str, trials: int) -> list:
        found = []
        for k in range(1, trials + 1):
            paths = sorted((self.out / f"t{k}").glob(f"{SKILL}/*/{arm}/summary.json"))
            self.assertEqual(len(paths), 1, f"t{k}/{arm}: {paths}")
            found.append(json.loads(paths[0].read_text(encoding="utf-8")))
        return found

    def _assert_nothing_ran(self, proc):
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertEqual(self._calls(), [],
                         "a refusal must happen before any CLI call")

    # -- 1. preflight: the environment -----------------------------------

    def test_refuses_each_credential_or_endpoint_variable(self):
        registry = self._registry()
        for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                     "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_BASE_URL"):
            for value in ("set", ""):
                with self.subTest(name=name, value=value):
                    proc = self._run(str(EVAL_DIR), "--trials", "1",
                                     "--registry", f"agentskills={registry}",
                                     env_extra={name: value})
                    self._assert_nothing_ran(proc)
                    self.assertIn(name, proc.stderr)
                    self.assertFalse(self.out.exists())

    def test_refuses_a_fixture_whose_env_block_names_a_refused_variable(self):
        # run_eval.agent_env applies a fixture's `env:` block LAST, over its
        # allowlist, so this is the one way a refused name could still reach
        # an arm after the caller's own environment passed the check.
        registry = self._registry()
        for name in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN",
                     "CLAUDE_CODE_USE_BEDROCK", "AWS_BEARER_TOKEN_BEDROCK",
                     "GOOGLE_APPLICATION_CREDENTIALS", "CLAUDE_CONFIG_DIR"):
            with self.subTest(name=name):
                eval_dir = self.root / f"fixture-{name}"
                shutil.copytree(EVAL_DIR, eval_dir)
                fixture_path = eval_dir / "fixture.yaml"
                fixture = yaml.safe_load(fixture_path.read_text(encoding="utf-8"))
                fixture["env"] = {name: "example-value", "HARMLESS": "1"}
                fixture_path.write_text(yaml.safe_dump(fixture), encoding="utf-8")
                proc = self._run(str(eval_dir), "--trials", "1",
                                 "--registry", f"agentskills={registry}")
                self._assert_nothing_ran(proc)
                self.assertIn(name, proc.stderr)
                self.assertIn("env:", proc.stderr)
                self.assertNotIn("example-value", proc.stderr)
                self.assertFalse(self.out.exists())

    def _parser_blocker(self) -> Path:
        """A directory whose `tree_sitter` cannot be imported, for PYTHONPATH."""
        package = self.root / "no-parser" / "tree_sitter"
        package.mkdir(parents=True, exist_ok=True)
        (package / "__init__.py").write_text(
            "raise ImportError('deliberately absent')\n", encoding="utf-8")
        return package.parent

    def _guard_fixture(self, with_guard_check: bool,
                       check_type: str = "shell_staged_tool_guard") -> Path:
        eval_dir = self.root / "fixture-guard"
        shutil.copytree(EVAL_DIR, eval_dir)
        if with_guard_check:
            path = eval_dir / "fixture.yaml"
            fixture = yaml.safe_load(path.read_text(encoding="utf-8"))
            check = {"id": "guard", "type": check_type, "paths": ["hook.sh"]}
            if check_type == "shell_staged_tool_guard":
                check["tools"] = ["gofmt"]
            fixture["objective_checks"].append(check)
            path.write_text(yaml.safe_dump(fixture), encoding="utf-8")
        return eval_dir

    def test_refuses_a_parser_backed_fixture_when_the_parser_cannot_import(self):
        # A missing parser must stop the run before any CLI call, naming the
        # pins and the pip command, not spend trials that cannot be scored.
        registry = self._registry()
        for check_type in ("shell_staged_tool_guard", "shell_capture_safe"):
            with self.subTest(check_type=check_type):
                shutil.rmtree(self.root / "fixture-guard", ignore_errors=True)
                self.log.unlink(missing_ok=True)
                proc = self._run(str(self._guard_fixture(True, check_type)),
                                 "--trials", "1",
                                 "--registry", f"agentskills={registry}",
                                 env_extra={"PYTHONPATH": str(self._parser_blocker())})
                self._assert_nothing_ran(proc)
                for needle in ("tree-sitter==0.26.0", "tree-sitter-bash==0.25.1",
                               "pip install", "Nothing run"):
                    self.assertIn(needle, proc.stderr)
                self.assertFalse(self.out.exists())

    def test_a_fixture_without_a_parser_backed_check_does_not_need_the_parser(self):
        registry = self._registry()
        proc = self._run(str(self._guard_fixture(False)), "--trials", "1",
                         "--registry", f"agentskills={registry}",
                         env_extra={"PYTHONPATH": str(self._parser_blocker())})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_scorer_dependency_check_in_process(self):
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        self.addCleanup(sys.path.remove, str(REPO_ROOT / "scripts"))
        import local_eval
        guarded = {"fixture": {"skill": "s", "objective_checks": [
            {"id": "g", "type": "shell_staged_tool_guard"}]}, "name": None}
        plain = {"fixture": {"skill": "s", "objective_checks": [
            {"id": "f", "type": "file_count"}]}, "name": None}
        # A watcher-shaped fixture: a parser-backed check beside plain ones.
        real = {"skill": "ci-watcher-loops", "objective_checks": [
            {"id": "w", "type": "file_count"},
            {"id": "c", "type": "shell_capture_safe"}]}
        watcher = {"fixture": real, "name": None}
        with mock.patch.object(local_eval.bash_ast, "parser_importable",
                               return_value=False):
            with self.assertRaises(local_eval.Refused):
                local_eval.check_scorer_dependencies([watcher])
            local_eval.check_scorer_dependencies([plain])
            with self.assertRaises(local_eval.Refused) as ctx:
                local_eval.check_scorer_dependencies([plain, guarded])
        self.assertIn("tree-sitter==0.26.0", str(ctx.exception))
        with mock.patch.object(local_eval.bash_ast, "parser_importable",
                               return_value=True):
            local_eval.check_scorer_dependencies([plain, guarded])

    def test_no_child_process_receives_a_refused_variable(self):
        # The only ANTHROPIC_ name any child may see is the probe's own
        # black-holed endpoint; the version call, every arm and every judge
        # call see none, and nothing ever sees the OAuth token name.
        proc = self._run(str(EVAL_DIR), "--trials", "1",
                         "--registry", f"agentskills={self._registry()}")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        calls = self._calls()
        self.assertEqual({c["role"] for c in calls},
                         {"version", "probe", "agent", "judge"})
        for call in calls:
            with self.subTest(role=call["role"], n=call["n"]):
                expected = (["ANTHROPIC_BASE_URL"] if call["role"] == "probe"
                            else [])
                self.assertEqual(call["secret_env"], expected)

    # -- 1. preflight: where results go ----------------------------------

    def test_refuses_a_results_dir_inside_this_checkout(self):
        name = f"local-eval-refusal-{self.root.name}"
        link = self.root / "link"
        link.symlink_to(REPO_ROOT, target_is_directory=True)
        dangling = self.root / "dangling"
        dangling.symlink_to(REPO_ROOT / name)
        for out in (REPO_ROOT / name, REPO_ROOT / "results" / name,
                    link / name, self.root / "link" / "evals" / ".." / name,
                    dangling):
            with self.subTest(out=str(out)):
                self.assertFalse((REPO_ROOT / name).exists())
                proc = self._run(str(EVAL_DIR), "--trials", "1", results_dir=out)
                self._assert_nothing_ran(proc)
                self.assertIn("inside this checkout", proc.stderr)
                self.assertFalse((REPO_ROOT / name).exists())
                self.assertFalse((REPO_ROOT / "results" / name).exists())

    def test_refuses_a_results_dir_inside_another_git_work_tree(self):
        other = self.root / "other-repo"
        other.mkdir()
        _git(other, "init", "-q")
        proc = self._run(str(EVAL_DIR), "--trials", "1",
                         results_dir=other / "nested" / "out")
        self._assert_nothing_ran(proc)
        self.assertIn("git work tree", proc.stderr)
        self.assertFalse((other / "nested").exists())

    def test_refuses_a_results_dir_inside_a_git_directory(self):
        other = self.root / "other-repo"
        other.mkdir()
        _git(other, "init", "-q")
        proc = self._run(str(EVAL_DIR), "--trials", "1",
                         results_dir=other / ".git" / "nested" / "out")
        self._assert_nothing_ran(proc)
        self.assertIn("git", proc.stderr)
        self.assertFalse((other / ".git" / "nested").exists())

    def test_the_checkout_guard_stands_without_a_git_work_tree(self):
        # In a checkout with no .git (an archive export) the work-tree probe
        # cannot fire, so the path comparison alone must refuse. Patch
        # REPO_ROOT to a non-repository and call the guard in-process.
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        self.addCleanup(sys.path.remove, str(REPO_ROOT / "scripts"))
        import local_eval
        fake_root = self.root / "archive-export"
        (fake_root / "results").mkdir(parents=True)
        link = self.root / "into-archive"
        link.symlink_to(fake_root, target_is_directory=True)
        with mock.patch.object(local_eval, "REPO_ROOT", fake_root):
            for out in (fake_root / "results" / "x", link / "results" / "x",
                        fake_root / "evals" / ".." / "x"):
                with self.subTest(out=str(out)):
                    with self.assertRaises(local_eval.Refused) as ctx:
                        local_eval.check_results_dir(out)
                    self.assertIn("inside this checkout", str(ctx.exception))
            outside = self.root / "elsewhere" / "x"
            self.assertEqual(local_eval.check_results_dir(outside), outside)

    def test_refuses_a_results_dir_that_already_holds_files(self):
        self.out.mkdir()
        (self.out / "earlier.txt").write_text("x\n", encoding="utf-8")
        proc = self._run(str(EVAL_DIR), "--trials", "1",
                         "--registry", f"agentskills={self._registry()}")
        self._assert_nothing_ran(proc)
        self.assertIn("already exists", proc.stderr)
        self.assertEqual(sorted(p.name for p in self.out.iterdir()),
                         ["earlier.txt"])

    def test_persistent_root_check_still_refuses_a_repository(self):
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        self.addCleanup(sys.path.remove, str(REPO_ROOT / "scripts"))
        import local_eval
        self.out.mkdir()
        (self.out / "earlier.txt").write_text("x\n", encoding="utf-8")
        self.assertEqual(local_eval.check_results_dir(self.out, require_empty=False),
                         self.out)
        with self.assertRaises(local_eval.Refused):
            local_eval.check_results_dir(REPO_ROOT / "results", require_empty=False)

    def test_refuses_a_guidance_fixture_and_out_of_range_trials(self):
        for args, message in (((str(GUIDANCE_FIXTURE), "--trials", "1"),
                               "skill fixtures only"),
                              ((str(EVAL_DIR), "--trials", "0"), "--trials"),
                              ((str(EVAL_DIR), "--trials", "21"), "--trials")):
            with self.subTest(args=args):
                proc = self._run(*args)
                self._assert_nothing_ran(proc)
                self.assertIn(message, proc.stderr)
                self.assertFalse(self.out.exists())

    # -- 3. contamination probe ------------------------------------------

    def _assert_contaminated(self, proc, expected_copy):
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        roles = [c["role"] for c in self._calls()]
        self.assertIn("probe", roles)
        self.assertNotIn("agent", roles, "no arm may run after contamination")
        self.assertNotIn("judge", roles)
        manifest = self._json("manifest.json")
        self.assertEqual(manifest["exhibit"], EXHIBIT)
        probe = manifest["contamination_probe"]
        self.assertTrue(probe["skill_under_test_visible"])
        self.assertIn(expected_copy, probe["visible_skills"])
        self.assertEqual(probe["skill_under_test_copies"], [expected_copy])
        self.assertTrue(manifest["status"].startswith("refused"))
        self.assertEqual(sorted(p.name for p in self.out.iterdir()),
                         ["LOCAL_EXHIBIT", "manifest.json"])

    def test_a_user_level_copy_of_the_skill_fails_the_run(self):
        _plant_skill(self.home / ".claude" / "skills", SKILL)
        proc = self._run(str(EVAL_DIR), "--trials", "1",
                         "--registry", f"agentskills={self._registry()}")
        self._assert_contaminated(proc, SKILL)

    def test_a_plugin_copy_of_the_skill_fails_the_run(self):
        install = self.root / "plugin-install"
        _plant_skill(install / "skills", SKILL)
        plugins = self.home / ".claude" / "plugins"
        plugins.mkdir(parents=True)
        (plugins / "installed_plugins.json").write_text(json.dumps(
            {"version": 2, "plugins": {"some-bundle@market": [
                {"scope": "user", "installPath": str(install),
                 "version": "1.0.0"}]}}), encoding="utf-8")
        proc = self._run(str(EVAL_DIR), "--trials", "1",
                         "--registry", f"agentskills={self._registry()}")
        self._assert_contaminated(proc, f"some-bundle:{SKILL}")

    def test_a_skill_whose_name_only_contains_the_skill_under_test_is_not_contamination(self):
        # The near miss: the match is the whole name (bare or after the last
        # `:`), so a longer name sharing a prefix does not stop the run.
        near_miss = f"{SKILL}-extra"
        _plant_skill(self.home / ".claude" / "skills", near_miss)
        proc = self._run(str(EVAL_DIR), "--trials", "1", "--no-judge",
                         "--registry", f"agentskills={self._registry()}")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        probe = self._json("manifest.json")["contamination_probe"]
        self.assertIn(near_miss, probe["visible_skills"])
        self.assertFalse(probe["skill_under_test_visible"])

    # -- 2, 4, 5, 6: a clean run -----------------------------------------

    def test_timestamp_is_forwarded_to_every_trial_and_validated(self):
        stamp = "20261004T120000Z"
        proc = self._run(str(EVAL_DIR), "--trials", "2", "--no-judge",
                         "--timestamp", stamp,
                         "--registry", f"agentskills={self._registry()}")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self._json("manifest.json")["invocation"]["timestamp"], stamp)
        for k in (1, 2):
            self.assertEqual(len(list((self.out / f"t{k}").glob(
                f"{SKILL}/{stamp}/**/with_skill/summary.json"))), 1)
        self.assertTrue((self.out / "LOCAL_EXHIBIT").is_file())
        self.log.unlink(missing_ok=True)
        self.out = self.root / "bad-timestamp"
        invalid = self._run(str(EVAL_DIR), "--timestamp", "../escape")
        self._assert_nothing_ran(invalid)
        self.assertIn("--timestamp", invalid.stderr)
        self.assertFalse(self.out.exists())

    def test_a_clean_run_records_identity_runs_n_trials_and_aggregates(self):
        registry = self._registry()
        # A RELATIVE registry path, resolved from the caller's cwd (the test
        # root), while run_eval itself runs from the repository root.
        proc = self._run(str(EVAL_DIR), "--registry", "agentskills=registry")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn(EXHIBIT, proc.stdout, "the console summary is labeled too")

        calls = self._calls()
        roles = [c["role"] for c in calls]
        self.assertEqual(roles.count("probe"), 1)
        self.assertLess(roles.index("probe"), roles.index("agent"),
                        "the probe must run before any arm")
        self.assertEqual(roles.count("agent"), 6, "3 trials x 2 arms")
        self.assertEqual(roles.count("judge"), 6)
        probe_call = calls[roles.index("probe")]
        argv = probe_call["argv"]
        self.assertEqual(argv[argv.index("--setting-sources") + 1], "project")
        self.assertEqual(probe_call["cwd_entries"], [".git"])
        self.assertEqual(probe_call["env"]["HOME"], str(self.home))
        self.assertTrue(probe_call["env"]["ANTHROPIC_BASE_URL"].startswith(
            "http://127.0.0.1:"))

        manifest = self._json("manifest.json")
        self.assertEqual(manifest["exhibit"], EXHIBIT)
        self.assertEqual(manifest["status"], "completed")
        self.assertRegex(manifest["started_at"],
                         r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        version = subprocess.run([sys.executable, str(FAKE_CLAUDE), "--version"],
                                 capture_output=True, text=True).stdout
        self.assertEqual(manifest["harness"]["claude_version"],
                         version.splitlines()[0].strip())
        # EVAL_DIR pins neither model (#371 item 5): the committed roster's
        # first arm and the judge chosen for it, a tier above (ADR 0013),
        # are what the run selects. The judge is worked out here from the
        # ladder, not by `judge_policy.judge_for_arm`, which this checks.
        roster = yaml.safe_load((REPO_ROOT / "evals" / "roster.yml").read_text(encoding="utf-8"))
        arm_ids = [arm["id"] for arm in roster["arms"]]
        sys.path.insert(0, str(REPO_ROOT / "harness"))
        self.addCleanup(sys.path.remove, str(REPO_ROOT / "harness"))
        import roster as tiers
        rungs = tiers.tier_rungs(tiers.load_policy(
            REPO_ROOT / "evals" / "roster-policy.yml"))
        tier = tiers.rung_of(arm_ids[0], rungs)
        placed = [(tiers.rung_of(arm, rungs), arm) for arm in arm_ids]
        above = sorted((rung, arm) for rung, arm in placed
                       if rung is not None and rung > tier)
        lowest = [arm for rung, arm in above if rung == above[0][0]]
        # One arm in the tier above (or none): nothing here works out "newest".
        assert len(lowest) <= 1, lowest
        judge = lowest[0] if lowest else roster["judge"]["id"]
        assert tiers.rung_of(judge, rungs) > tier, (arm_ids[0], judge)
        self.assertEqual(manifest["fixtures"][0]["models"]["fixture_pins"],
                         {"model": None, "judge_model": None})
        self.assertEqual(manifest["fixtures"][0]["models"]["selected"],
                         {"agent": arm_ids[0], "judge": judge})
        head = _git(registry, "rev-parse", "HEAD").stdout.strip()
        self.assertEqual(manifest["fixtures"][0]["registry"],
                         {"name": "agentskills", "source": "--registry flag",
                          "sha": head, "dirty": False})
        own = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
                             capture_output=True, text=True)
        self.assertEqual(manifest["skills_evals"]["sha"],
                         own.stdout.strip() if own.returncode == 0 else None)
        probe = manifest["contamination_probe"]
        self.assertFalse(probe["skill_under_test_visible"])
        self.assertEqual(probe["setting_sources"], "project")
        self.assertIsInstance(probe["visible_skills"], list)
        self.assertNotIn(SKILL, probe["visible_skills"])
        self.assertEqual([(t["trial"], t["exit_code"]) for t in manifest["trials"]],
                         [(1, 0), (2, 0), (3, 0)])

        # 6: transcripts stay where run_eval wrote them, and are listed.
        raw = sorted(p.relative_to(self.out).as_posix()
                     for p in self.out.rglob("raw.json"))
        self.assertEqual(len(raw), 6)
        self.assertTrue(all(re.match(r"^t[123]/", p) for p in raw), raw)
        self.assertEqual(manifest["transcripts"]["files"], raw)
        self.assertIs(manifest["transcripts"]["local_only"], True)
        self.assertIn("LOCAL-ONLY", manifest["transcripts"]["note"])
        self.assertEqual(sorted(p.name for p in self.out.iterdir()),
                         ["LOCAL_EXHIBIT", "aggregate.json", "manifest.json",
                          "t1", "t2", "t3"])
        for k in (1, 2, 3):
            self.assertIn(EXHIBIT, (self.out / f"t{k}" / "LOCAL_EXHIBIT").read_text(
                encoding="utf-8"))
        self.assertIn(EXHIBIT, (self.out / "LOCAL_EXHIBIT").read_text(encoding="utf-8"))

        # 5: the aggregate, recomputed here from the trial summaries.
        aggregate = self._json("aggregate.json")
        self.assertEqual(aggregate["exhibit"], EXHIBIT)
        self.assertEqual(sorted(aggregate["arms"]), ["with_skill", "without_skill"])
        for arm in ("with_skill", "without_skill"):
            with self.subTest(arm=arm):
                summaries = self._summaries(arm, 3)
                got = aggregate["arms"][arm]
                self.assertEqual((got["n"], got["errors"], got["scored"]), (3, 0, 3))
                overall = [s["judge"]["overall"] for s in summaries]
                self.assertGreater(len(set(overall)), 1,
                                   "the dispatcher must vary the judge per call")
                self.assertEqual(got["judge"]["overall"],
                                 {"n": 3, "mean": sum(overall) / 3,
                                  "min": min(overall), "max": max(overall)})
                completeness = [d["score"] for s in summaries
                                for d in s["judge"]["dimensions"]
                                if d["name"] == "Completeness"]
                self.assertEqual(got["judge"]["dimensions"]["Completeness"]["mean"],
                                 sum(completeness) / 3)
                totals = [sum(c["passed"] for c in s["objective_checks"])
                          for s in summaries]
                self.assertEqual(got["objective"]["total"],
                                 {"n": 3, "mean": sum(totals) / 3,
                                  "min": min(totals), "max": max(totals)})
                for check_id in {c["id"] for c in summaries[0]["objective_checks"]}:
                    passed = sum(c["passed"] for s in summaries
                                 for c in s["objective_checks"] if c["id"] == check_id)
                    self.assertEqual(got["objective"]["checks"][check_id],
                                     {"passed": passed, "n": 3,
                                      "pass_rate": passed / 3})
                costs = [s["agent"]["cost_usd"] for s in summaries]
                self.assertEqual(got["cost_usd"]["sum"], sum(costs))

    # -- 4: a trial that errors ------------------------------------------

    def test_an_errored_trial_is_recorded_counted_and_fails_the_exit_code(self):
        # Agent call 3 is trial 2's with_skill arm (arms run with_skill first).
        proc = self._run(str(EVAL_DIR), "--trials", "3",
                         "--registry", f"agentskills={self._registry()}",
                         fail_agent_call=3)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        manifest = self._json("manifest.json")
        self.assertEqual([t["exit_code"] for t in manifest["trials"]], [0, 2, 0])
        self.assertIn("t2/run_eval.log", manifest["trials"][1]["error"])
        self.assertTrue((self.out / "t2" / "run_eval.log").is_file())
        self.assertEqual(manifest["status"], "completed with errors")
        arms = self._json("aggregate.json")["arms"]
        self.assertEqual((arms["with_skill"]["n"], arms["with_skill"]["errors"],
                          arms["with_skill"]["scored"]), (3, 1, 2))
        self.assertEqual(arms["with_skill"]["error_trials"],
                         [{"trial": 2, "error": "nonzero_exit"}])
        self.assertEqual(arms["with_skill"]["judge"]["overall"]["n"], 2)
        self.assertEqual((arms["without_skill"]["n"],
                          arms["without_skill"]["errors"]), (3, 0))

    # -- 4: pass-through -------------------------------------------------

    def test_arm_and_no_judge_pass_through(self):
        proc = self._run(str(EVAL_DIR), "--trials", "1", "--arm", "without_skill",
                         "--no-judge")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        roles = [c["role"] for c in self._calls()]
        self.assertEqual(roles.count("agent"), 1)
        self.assertNotIn("judge", roles)
        self.assertEqual(list((self.out / "t1" / SKILL).glob("*/with_skill")), [])
        aggregate = self._json("aggregate.json")
        self.assertEqual(sorted(aggregate["arms"]), ["without_skill"])
        self.assertIsNone(aggregate["arms"]["without_skill"]["judge"])
        manifest = self._json("manifest.json")
        self.assertEqual(manifest["invocation"]["arm"], "without_skill")
        self.assertIs(manifest["invocation"]["no_judge"], True)
        self.assertIsNone(manifest["fixtures"][0]["models"]["selected"]["judge"])

    # -- 3, 5: never publishes; argv lists; no shell ---------------------

    def _shim_dir(self) -> tuple[Path, Path]:
        """A directory of `gh` and `git` shims that log each call (to the
        returned log) and then run the real git, or fail for gh."""
        shims = self.root / "shims"
        shims.mkdir()
        log = self.root / "shim-calls.jsonl"
        for tool, real in (("gh", None), ("git", shutil.which("git"))):
            shim = shims / tool
            shim.write_text(SHIM.format(python=sys.executable, log=str(log),
                                        tool=tool, real=real), encoding="utf-8")
            shim.chmod(0o755)
        return shims, log

    @staticmethod
    def _tree_snapshot() -> dict:
        """{path: mtime_ns} for every file in this checkout outside .git and
        bytecode caches, so a created OR rewritten file shows."""
        snapshot = {}
        for root, dirs, files in os.walk(REPO_ROOT):
            dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
            for name in files:
                path = os.path.join(root, name)
                snapshot[path] = os.lstat(path).st_mtime_ns
        return snapshot

    @staticmethod
    def _git_subcommand(argv: list) -> str | None:
        args, i = list(argv), 0
        while i < len(args):
            if args[i] in ("-C", "-c"):
                i += 2
            elif args[i].startswith("-"):
                i += 1
            else:
                return args[i]
        return None

    def test_a_run_never_calls_gh_or_pushes_and_writes_only_to_the_results_dir(self):
        shims, log = self._shim_dir()
        path = os.pathsep.join([str(shims), self._env(self._dispatcher())["PATH"]])
        before = self._tree_snapshot()
        proc = self._run(str(EVAL_DIR), "--trials", "1", "--no-judge",
                         "--registry", f"agentskills={self._registry()}",
                         env_extra={"PATH": path})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        calls = [json.loads(line) for line in
                 log.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(c["tool"] == "git" for c in calls),
                        "the shim must be on the path the run really used")
        self.assertEqual([c for c in calls if c["tool"] == "gh"], [])
        subcommands = {self._git_subcommand(c["argv"]) for c in calls
                       if c["tool"] == "git"}
        self.assertEqual(subcommands & {"push", "fetch", "pull", "remote",
                                        "send-pack", "checkout", "switch"},
                         set())
        self.assertEqual(self._tree_snapshot(), before,
                         "the repository must be untouched by a run")

    def test_the_wrapper_builds_every_subprocess_as_an_argv_list(self):
        # A shape check, so it parses the module (an AST, not a regex).
        source = (REPO_ROOT / "scripts" / "local_eval.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        list_names = {t.id for node in ast.walk(tree)
                      if isinstance(node, ast.Assign)
                      and isinstance(node.value, ast.List)
                      for t in node.targets if isinstance(t, ast.Name)}
        spawned = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = ast.unparse(node.func)
            self.assertNotIn(func, ("os.system", "os.popen", "os.spawnl",
                                    "os.execl"), func)
            if func.split(".")[0] != "subprocess":
                continue
            spawned += 1
            first = node.args[0] if node.args else None
            self.assertTrue(
                isinstance(first, ast.List)
                or (isinstance(first, ast.Name) and first.id in list_names),
                f"line {node.lineno}: {func} must take an argv list")
            for kw in node.keywords:
                if kw.arg == "shell":
                    self.fail(f"line {node.lineno}: {func} passes shell=")
        self.assertGreaterEqual(spawned, 2)
        # Nothing here may name a publishing command, in an argv or anywhere
        # else a string can be built (docstrings excluded).
        docstrings = {id(n.value) for n in ast.walk(tree)
                      if isinstance(n, ast.Expr)
                      and isinstance(n.value, ast.Constant)}
        words = {n.value for n in ast.walk(tree)
                 if isinstance(n, ast.Constant) and isinstance(n.value, str)
                 and id(n) not in docstrings}
        self.assertEqual(words & {"gh", "push", "workflow", "dispatch"}, set())

    # -- 1: provider selection and credentials, by rule ------------------

    def test_refuses_any_provider_selection_or_credential_variable(self):
        names = ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
                 "CLAUDE_CODE_USE_FOUNDRY", "AWS_BEARER_TOKEN_BEDROCK",
                 "AWS_ACCESS_KEY_ID", "AWS_PROFILE",
                 "GOOGLE_APPLICATION_CREDENTIALS", "GCLOUD_PROJECT",
                 "CLOUDSDK_CORE_PROJECT", "AZURE_CLIENT_ID",
                 "CLAUDE_CONFIG_DIR", "ACME_API_KEY", "acme_auth_token",
                 "ACME_ACCESS_KEY", "ACME_SECRET", "ACME_BEARER")
        for name in names:
            with self.subTest(name=name):
                proc = self._run(str(EVAL_DIR), "--trials", "1",
                                 env_extra={name: "sentinel-value-1234"})
                self._assert_nothing_ran(proc)
                self.assertIn(name, proc.stderr)
                self.assertNotIn("sentinel-value-1234", proc.stderr)
                self.assertFalse(self.out.exists())

    def test_an_unlisted_variable_never_reaches_any_child(self):
        # The refusal list cannot name every variable; the children's
        # environment is an allow-list built here, so this one (on no refusal
        # list) must not appear for the version call, the probe, an arm or
        # the judge.
        allowed = {"PATH", "HOME", "LANG", "LANGUAGE", "TERM", "TMPDIR", "TZ",
                   "USER", "LOGNAME", "SHELL", "HTTP_PROXY", "HTTPS_PROXY",
                   "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
                   "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE", "SSL_CERT_DIR",
                   "CLAUDE_BIN", "SKILLS_EVALS_REGISTRIES", "AGENTSKILLS_DIR",
                   # SET by child_environment, never inherited: auto-memory
                   # off, so no child writes into the real HOME.
                   "CLAUDE_CODE_DISABLE_AUTO_MEMORY"}
        proc = self._run(str(EVAL_DIR), "--trials", "1",
                         "--registry", f"agentskills={self._registry()}",
                         env_extra={"SOME_UNLISTED_VAR": "1",
                                    "CLAUDECODE": "1", "LC_ALL": "C.UTF-8"})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        calls = self._calls()
        self.assertEqual({c["role"] for c in calls},
                         {"version", "probe", "agent", "judge"})
        for call in calls:
            with self.subTest(role=call["role"], n=call["n"]):
                for name in ("SOME_UNLISTED_VAR", "CLAUDECODE"):
                    self.assertNotIn(name, call["env_names"])
                self.assertIn("CLAUDE_CODE_DISABLE_AUTO_MEMORY", call["env_names"])
                if call["role"] in ("version", "judge"):
                    # these inherit the wrapper's whole environment
                    extra = {n for n in call["env_names"]
                             if n not in allowed and not n.startswith("LC_")}
                    self.assertEqual(extra, set())

    # -- 1b: settings files the judge loads ------------------------------

    def _settings(self, content, name="settings.json", home=None):
        directory = (home or self.home) / ".claude"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_text(
            content if isinstance(content, str) else json.dumps(content),
            encoding="utf-8")

    def test_refuses_a_settings_file_naming_a_credential_source(self):
        cases = (
            ("settings.json", {"apiKeyHelper": "sentinel-helper"}, "apiKeyHelper"),
            ("settings.json", {"awsAuthRefresh": "sentinel-helper"}, "awsAuthRefresh"),
            ("settings.local.json", {"awsCredentialExport": "sentinel-helper"},
             "awsCredentialExport"),
            ("settings.json", {"env": {"ANTHROPIC_API_KEY": "sentinel-helper"}},
             "env.ANTHROPIC_API_KEY"),
            ("settings.json", {"env": {"AWS_ACCESS_KEY_ID": "sentinel-helper"}},
             "env.AWS_ACCESS_KEY_ID"),
            ("settings.json", {"env": {"CLAUDE_CODE_USE_VERTEX": "sentinel-helper"}},
             "env.CLAUDE_CODE_USE_VERTEX"),
            ("settings.json", "{not json sentinel-helper", "not valid JSON"),
            ("settings.json", ["sentinel-helper"], "not a JSON object"))
        for name, content, expected in cases:
            with self.subTest(name=name, expected=expected):
                self._settings(content, name)
                proc = self._run(str(EVAL_DIR), "--trials", "1")
                self._assert_nothing_ran(proc)
                self.assertIn(str(self.home / ".claude" / name), proc.stderr)
                self.assertIn(expected, proc.stderr)
                self.assertNotIn("sentinel-helper", proc.stderr)
                self.assertFalse(self.out.exists())
                (self.home / ".claude" / name).unlink()

    def test_a_settings_file_without_a_credential_source_does_not_stop_the_run(self):
        self._settings({"theme": "dark", "env": {"EXAMPLE_FLAG": "1"},
                        "permissions": {"allow": ["Bash(ls:*)"]}})
        proc = self._run(str(EVAL_DIR), "--trials", "1", "--no-judge",
                         "--registry", f"agentskills={self._registry()}")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_the_settings_check_covers_this_checkout_and_managed_policy(self):
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        self.addCleanup(sys.path.remove, str(REPO_ROOT / "scripts"))
        import local_eval
        fake_repo, managed = self.root / "fake-repo", self.root / "managed"
        (fake_repo / ".claude").mkdir(parents=True)
        (managed / "dropins").mkdir(parents=True)
        home = self.root / "settings-home"
        home.mkdir()
        cases = (
            (fake_repo / ".claude" / "settings.json", {"apiKeyHelper": "x"}),
            (fake_repo / ".claude" / "settings.local.json", {"awsAuthRefresh": "x"}),
            (managed / "managed-settings.json", {"apiKeyHelper": "x"}),
            (managed / "dropins" / "10-extra.json",
             {"env": {"AZURE_CLIENT_SECRET": "x"}}))
        with mock.patch.object(local_eval, "REPO_ROOT", fake_repo), \
                mock.patch.object(local_eval, "MANAGED_SETTINGS_FILES",
                                  (managed / "managed-settings.json",)), \
                mock.patch.object(local_eval, "MANAGED_SETTINGS_DROPINS",
                                  (managed / "dropins",)):
            local_eval.check_user_settings(home)  # nothing there: passes
            for path, content in cases:
                with self.subTest(path=str(path)):
                    path.write_text(json.dumps(content), encoding="utf-8")
                    with self.assertRaises(local_eval.Refused) as ctx:
                        local_eval.check_user_settings(home)
                    self.assertIn(str(path), str(ctx.exception))
                    path.unlink()

    # -- 2: git cannot answer, or a .git sits above ----------------------

    def test_a_git_that_cannot_answer_is_a_refusal_not_no_repository(self):
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        self.addCleanup(sys.path.remove, str(REPO_ROOT / "scripts"))
        import local_eval
        probe = self.root / "plain"
        probe.mkdir()
        for stderr in ("fatal: detected dubious ownership in repository at '/x'\n",
                       "fatal: cannot use bare repository '/x' "
                       "(safe.bareRepository is 'explicit')\n"):
            with self.subTest(stderr=stderr):
                failed = subprocess.CompletedProcess([], 128, "", stderr)
                with mock.patch.object(local_eval, "_git", return_value=failed):
                    with self.assertRaises(local_eval.Refused) as ctx:
                        local_eval.check_results_dir(probe / "out")
                self.assertIn("refused to say", str(ctx.exception))
        with mock.patch.object(local_eval, "_git", side_effect=OSError("no git")):
            with self.assertRaises(local_eval.Refused):
                local_eval.check_results_dir(probe / "out")
        # and the genuine "not a repository" answer still lets it through
        self.assertEqual(local_eval.check_results_dir(probe / "out"),
                         probe / "out")

    def test_a_git_entry_in_a_parent_refuses_without_asking_git(self):
        # An empty .git directory is not a repository to git ("not a git
        # repository"), so only the git-independent parent walk can refuse.
        tree = self.root / "looks-like-a-repo"
        (tree / ".git").mkdir(parents=True)
        proc = self._run(str(EVAL_DIR), "--trials", "1",
                         results_dir=tree / "deeper" / "out")
        self._assert_nothing_ran(proc)
        self.assertIn("holds a git repository", proc.stderr)
        self.assertFalse((tree / "deeper").exists())

    # -- N1: a local trial tree is not badge input -----------------------

    def _make_badge(self, name, results_dir):
        badge = self.root / "badge.json"
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "make_badge.py"), name,
             "--results-dir", str(results_dir), "--out", str(badge)],
            capture_output=True, text=True, env=self._env(self._dispatcher()),
            cwd=str(self.root), timeout=120)
        return proc, badge

    def test_local_trial_summaries_are_stamped_and_refused_as_badge_input(self):
        for eval_dir, skill in ((EVAL_DIR, SKILL),
                                (FIXTURE_EVALS / "writing-adrs" / "bootstrap",
                                 "writing-adrs")):
            with self.subTest(skill=skill):
                if self.out.exists():
                    shutil.rmtree(self.out)
                shutil.rmtree(self.root / "registry", ignore_errors=True)
                registry = self._registry(skill)
                proc = self._run(str(eval_dir), "--trials", "1", "--no-judge",
                                 "--registry", f"agentskills={registry}")
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                summaries = sorted(self.out.rglob("summary.json"))
                self.assertEqual(len(summaries), 2)
                for path in summaries:
                    self.assertIs(json.loads(path.read_text(
                        encoding="utf-8"))["local_exhibit"], True, str(path))
                self._remove_markers()  # isolate the stamp from the marker
                badge_proc, badge = self._make_badge(skill, self.out / "t1")
                self.assertNotEqual(badge_proc.returncode, 0)
                self.assertIn("local_exhibit: true", badge_proc.stderr)
                self.assertFalse(badge.exists())

    def test_an_unstamped_summary_is_still_badge_input(self):
        registry = self._registry()
        proc = self._run(str(EVAL_DIR), "--trials", "1", "--no-judge",
                         "--registry", f"agentskills={registry}")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self._unstamp_summaries()
        self._remove_markers()  # now it looks like a published tree
        badge_proc, badge = self._make_badge(SKILL, self.out / "t1")
        self.assertEqual(badge_proc.returncode, 0, badge_proc.stderr)
        self.assertIn(f"skill eval: {SKILL}",
                      json.loads(badge.read_text(encoding="utf-8"))["label"])
        # ... and it reads exactly as it did before this change.
        baseline = self._baseline_make_badge()
        if baseline is None:
            self.skipTest("origin/main is not available to compare against")
        spec = importlib.util.spec_from_file_location("make_badge_baseline",
                                                      baseline)
        old_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(old_module)
        old_badge = old_module.build_badge(self.out / "t1", SKILL)
        # make_badge.py writes json.dumps(badge, indent=2, sort_keys=True)+"\n"
        self.assertEqual(
            badge.read_bytes(),
            (json.dumps(old_badge, indent=2, sort_keys=True) + "\n").encode("utf-8"))

    def _unstamp_summaries(self):
        for path in self.out.rglob("summary.json"):
            summary = json.loads(path.read_text(encoding="utf-8"))
            summary.pop("local_exhibit", None)  # what a published summary lacks
            path.write_text(json.dumps(summary), encoding="utf-8")

    def _remove_markers(self):
        for path in self.out.rglob("LOCAL_EXHIBIT"):
            path.unlink()

    def _baseline_make_badge(self):
        """make_badge.py as origin/main has it, or None when this checkout has
        no such ref (a shallow CI clone)."""
        shown = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "show",
             "origin/main:scripts/make_badge.py"], capture_output=True, text=True)
        if shown.returncode != 0:
            return None
        path = self.root / "make_badge_baseline.py"
        path.write_text(shown.stdout, encoding="utf-8")
        return path

    def test_the_marker_alone_makes_a_trial_tree_unusable_as_badge_input(self):
        proc = self._run(str(EVAL_DIR), "--trials", "1", "--no-judge",
                         "--registry", f"agentskills={self._registry()}")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self._unstamp_summaries()  # a kill before the stamp: marker only
        for results in (self.out / "t1", self.out):
            with self.subTest(results=str(results)):
                badge_proc, badge = self._make_badge(SKILL, results)
                self.assertNotEqual(badge_proc.returncode, 0)
                self.assertIn("inside a local exhibit", badge_proc.stderr)
                self.assertFalse(badge.exists())

    def test_a_marker_in_any_parent_directory_refuses_too(self):
        proc = self._run(str(EVAL_DIR), "--trials", "1", "--no-judge",
                         "--registry", f"agentskills={self._registry()}")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self._unstamp_summaries()
        # A copy of the trial tree with only the PARENT carrying the marker.
        parent = self.root / "marked-parent"
        shutil.copytree(self.out / "t1", parent / "deeper" / "t1")
        (parent / "LOCAL_EXHIBIT").write_text("x\n", encoding="utf-8")
        for marker in (parent / "deeper" / "t1" / "LOCAL_EXHIBIT",):
            marker.unlink()
        badge_proc, badge = self._make_badge(SKILL, parent / "deeper" / "t1")
        self.assertNotEqual(badge_proc.returncode, 0)
        self.assertIn("inside a local exhibit", badge_proc.stderr)
        self.assertFalse(badge.exists())

    def test_the_marker_is_written_before_the_trial_launches(self):
        # run_eval is the first thing to write under t<k>; the dispatcher logs
        # the agent's cwd, so look at what already sat in the trial dir's root.
        proc = self._run(str(EVAL_DIR), "--trials", "1", "--no-judge",
                         "--arm", "without_skill")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue((self.out / "LOCAL_EXHIBIT").is_file())
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        self.addCleanup(sys.path.remove, str(REPO_ROOT / "scripts"))
        import local_eval
        # Stand in for harness/run_eval.py with a script that records whether
        # the marker already sat in its --results-dir, then hands over to the
        # real one.
        seen = self.root / "marker-seen.txt"
        spy = self.root / "run_eval_spy.py"
        spy.write_text(
            "import os, sys\n"
            "out = sys.argv[sys.argv.index('--results-dir') + 1]\n"
            f"open({str(seen)!r}, 'a').write(\n"
            "    str(os.path.isfile(os.path.join(out, 'LOCAL_EXHIBIT'))) + '\\n')\n"
            f"os.execv(sys.executable, [sys.executable, {str(local_eval.RUN_EVAL)!r},"
            " *sys.argv[1:]])\n", encoding="utf-8")
        shutil.rmtree(self.out)
        env = self._env(self._dispatcher())
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(local_eval, "RUN_EVAL", spy), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            code = local_eval.main(
                [str(EVAL_DIR), "--results-dir", str(self.out), "--trials", "1",
                 "--no-judge", "--arm", "without_skill"])
        self.assertEqual(code, 0)
        self.assertEqual(seen.read_text(encoding="utf-8").split(), ["True"])

    def test_every_trial_launches_run_eval_under_the_recorded_mode(self):
        # The arms' CLI permission mode (#71) must reach each trial's
        # run_eval.py argv and be recorded in manifest.json, for the default
        # and for an explicit mode. A spy stands in for harness/run_eval.py,
        # logs its argv, then hands over to the real one.
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        self.addCleanup(sys.path.remove, str(REPO_ROOT / "scripts"))
        import local_eval
        argv_log = self.root / "run-eval-argv.jsonl"
        spy = self.root / "run_eval_argv_spy.py"
        spy.write_text(
            "import json, os, sys\n"
            f"open({str(argv_log)!r}, 'a').write(\n"
            "    json.dumps(sys.argv[1:]) + '\\n')\n"
            f"os.execv(sys.executable, [sys.executable, {str(local_eval.RUN_EVAL)!r},"
            " *sys.argv[1:]])\n", encoding="utf-8")
        env = self._env(self._dispatcher())
        for flag, mode in ((None, "auto"),
                           ("bypassPermissions", "bypassPermissions")):
            with self.subTest(mode=mode):
                argv_log.unlink(missing_ok=True)
                out = self.root / f"out-{mode}"
                extra = [] if flag is None else ["--permission-mode", flag]
                with mock.patch.dict(os.environ, env, clear=True), \
                        mock.patch.object(local_eval, "RUN_EVAL", spy), \
                        contextlib.redirect_stdout(io.StringIO()), \
                        contextlib.redirect_stderr(io.StringIO()):
                    code = local_eval.main(
                        [str(EVAL_DIR), "--results-dir", str(out), "--trials", "2",
                         "--no-judge", "--arm", "without_skill", *extra])
                self.assertEqual(code, 0)
                launches = [json.loads(line) for line in
                            argv_log.read_text(encoding="utf-8").splitlines()]
                self.assertEqual(len(launches), 2, launches)
                for argv in launches:
                    positions = [i for i, a in enumerate(argv)
                                 if a == "--permission-mode"]
                    self.assertEqual(len(positions), 1, argv)
                    self.assertEqual(argv[positions[0] + 1], mode, argv)
                    # The whole results dir, so trial 2's arms cannot read
                    # trial 1's transcripts and tool traces.
                    denied = [argv[i + 1] for i, a in enumerate(argv)
                              if a == "--read-deny"]
                    self.assertEqual(denied, [os.path.abspath(out)], argv)
                    # What kind of run this is (#370): a workstation, under
                    # the operator's own login, on this machine.
                    for name, value in (("--run-billing", "subscription"),
                                        ("--run-runner", "workstation"),
                                        ("--run-location", "local")):
                        self.assertEqual(argv[argv.index(name) + 1], value, argv)
                manifest = json.loads(
                    (out / "manifest.json").read_text(encoding="utf-8"))
                self.assertEqual(manifest["harness"]["permission_mode"], mode)
                # One run id for the whole invocation, shared by its trials.
                ids = {argv[argv.index("--run-id") + 1] for argv in launches}
                self.assertEqual(ids, {manifest["run_id"]})
                self.assertRegex(manifest["run_id"],
                                 r"\A[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}\Z")

    # -- nested fixtures (run_eval's #66 layout) -------------------------

    def _assert_clean(self, proc, trials, fixtures):
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        aggregate = self._json("aggregate.json")
        self.assertEqual(sorted(aggregate["fixtures"]), sorted(fixtures))
        for name, entry in aggregate["fixtures"].items():
            for arm, got in entry["arms"].items():
                with self.subTest(fixture=name, arm=arm):
                    self.assertEqual((got["n"], got["errors"], got["scored"]),
                                     (trials, 0, trials))
        return aggregate

    def test_a_flat_fixture_runs_clean_under_the_fixtures_key(self):
        proc = self._run(str(EVAL_DIR), "--trials", "2", "--no-judge",
                         "--registry", f"agentskills={self._registry()}")
        aggregate = self._assert_clean(proc, 2, ["(flat)"])
        self.assertEqual(aggregate["arms"], aggregate["fixtures"]["(flat)"]["arms"])

    def test_a_nested_fixture_named_by_its_own_directory_runs_clean(self):
        nested = FIXTURE_EVALS / "writing-adrs" / "bootstrap"
        proc = self._run(str(nested), "--trials", "2", "--no-judge",
                         "--registry",
                         f"agentskills={self._registry('writing-adrs')}")
        aggregate = self._assert_clean(proc, 2, ["bootstrap"])
        self.assertEqual(sorted(aggregate["arms"]), ["with_skill", "without_skill"])
        self.assertEqual(
            len(list(self.out.glob("t*/writing-adrs/*/bootstrap/*/summary.json"))), 4)

    def test_a_skill_directory_of_nested_fixtures_runs_all_of_them(self):
        skill_dir = FIXTURE_EVALS / "writing-adrs"
        registry = self._registry("writing-adrs")
        proc = self._run(str(skill_dir), "--trials", "2", "--no-judge",
                         "--registry", f"agentskills={registry}")
        aggregate = self._assert_clean(
            proc, 2, ["bootstrap", "existing-convention", "supersede",
                      "why-no-comment-trail"])
        self.assertNotIn("arms", aggregate, "several fixtures: no single `arms`")
        self.assertEqual(
            [f["name"] for f in self._json("manifest.json")["fixtures"]],
            ["bootstrap", "existing-convention", "supersede",
             "why-no-comment-trail"])

    def test_fixture_selects_one_nested_fixture_of_a_skill_directory(self):
        skill_dir = FIXTURE_EVALS / "writing-adrs"
        proc = self._run(str(skill_dir), "--fixture", "supersede", "--trials", "1",
                         "--no-judge", "--registry",
                         f"agentskills={self._registry('writing-adrs')}")
        self._assert_clean(proc, 1, ["supersede"])

    # -- review round 2 --------------------------------------------------

    def test_a_hostile_xdg_config_home_never_reaches_a_child(self):
        hostile = self.root / "xdg"
        (hostile / "claude").mkdir(parents=True)
        (hostile / "claude" / "settings.json").write_text(
            json.dumps({"apiKeyHelper": "/bin/example"}), encoding="utf-8")
        proc = self._run(str(EVAL_DIR), "--trials", "1",
                         "--registry", f"agentskills={self._registry()}",
                         env_extra={"XDG_CONFIG_HOME": str(hostile),
                                    "XDG_DATA_HOME": str(hostile),
                                    "XDG_STATE_HOME": str(hostile),
                                    "XDG_CACHE_HOME": str(hostile)})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        calls = self._calls()
        self.assertGreater(len(calls), 3)
        for call in calls:
            with self.subTest(role=call["role"], n=call["n"]):
                self.assertEqual([n for n in call["env_names"]
                                  if n.startswith("XDG_")], [])

    def test_proxy_and_ca_variables_and_identity_reach_the_children(self):
        passed = {"HTTPS_PROXY": "http://proxy.example.com:3128",
                  "http_proxy": "http://proxy.example.com:3128",
                  "NO_PROXY": "example.net", "NODE_EXTRA_CA_CERTS": "/x/ca.pem",
                  "SSL_CERT_FILE": "/x/ca.pem", "SSL_CERT_DIR": "/x/certs",
                  "USER": "someone", "LOGNAME": "someone", "SHELL": "/bin/sh"}
        proc = self._run(str(EVAL_DIR), "--trials", "1",
                         "--registry", f"agentskills={self._registry()}",
                         env_extra=passed)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        for call in self._calls():
            if call["role"] in ("version", "judge"):
                with self.subTest(role=call["role"]):
                    self.assertLessEqual(set(passed), set(call["env_names"]))

    def test_refuses_a_proxy_url_that_embeds_userinfo(self):
        for name in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
            for value in ("http://user:sentinel-pw@proxy.example.com:3128",
                          "http://sentinel-token@proxy.example.com/",
                          "user:sentinel-pw@proxy.example.com:3128"):
                with self.subTest(name=name, value=value):
                    proc = self._run(str(EVAL_DIR), "--trials", "1",
                                     env_extra={name: value})
                    self._assert_nothing_ran(proc)
                    self.assertIn(name, proc.stderr)
                    self.assertNotIn("sentinel", proc.stderr)
                    self.assertFalse(self.out.exists())

    def test_refuses_a_credential_source_in_a_seed_or_registry_settings_file(self):
        registry = self._registry()
        eval_dir = self.root / "fixture-with-seed-settings"
        shutil.copytree(EVAL_DIR, eval_dir)
        deep = eval_dir / "seed" / "some" / "sub" / ".claude"
        deep.mkdir(parents=True)
        for name, content, key in (
                ("settings.json", {"apiKeyHelper": "sentinel-helper"}, "apiKeyHelper"),
                ("settings.local.json", {"env": {"AWS_PROFILE": "x"}},
                 "env.AWS_PROFILE")):
            with self.subTest(seed_file=name):
                (deep / name).write_text(json.dumps(content), encoding="utf-8")
                proc = self._run(str(eval_dir), "--trials", "1",
                                 "--registry", f"agentskills={registry}")
                self._assert_nothing_ran(proc)
                self.assertIn(str(deep / name), proc.stderr)
                self.assertIn(key, proc.stderr)
                self.assertNotIn("sentinel-helper", proc.stderr)
                self.assertFalse(self.out.exists())
                (deep / name).unlink()
        (registry / ".claude").mkdir()
        for name, content, key in (
                ("settings.json", {"awsCredentialExport": "sentinel-helper"},
                 "awsCredentialExport"),
                ("settings.local.json", {"env": {"ANTHROPIC_API_KEY": "x"}},
                 "env.ANTHROPIC_API_KEY")):
            with self.subTest(registry_file=name):
                path = registry / ".claude" / name
                path.write_text(json.dumps(content), encoding="utf-8")
                proc = self._run(str(EVAL_DIR), "--trials", "1",
                                 "--registry", f"agentskills={registry}")
                self._assert_nothing_ran(proc)
                self.assertIn(str(path), proc.stderr)
                self.assertIn(key, proc.stderr)
                self.assertFalse(self.out.exists())
                path.unlink()

    def test_an_unreadable_or_undecodable_settings_file_is_a_refusal(self):
        target = self.home / ".claude" / "settings.json"
        target.parent.mkdir(parents=True)
        cases = [("a directory", lambda: target.mkdir(), "IsADirectoryError"),
                 ("not UTF-8", lambda: target.write_bytes(b"\xff\xfe{"),
                  "UnicodeDecodeError")]
        if os.geteuid() != 0:
            def unreadable():
                target.write_text("{}", encoding="utf-8")
                target.chmod(0)
            cases.append(("permission denied", unreadable, "PermissionError"))
        for label, make, error in cases:
            with self.subTest(case=label):
                make()
                proc = self._run(str(EVAL_DIR), "--trials", "1")
                self._assert_nothing_ran(proc)
                self.assertIn(str(target), proc.stderr)
                self.assertIn("cannot read settings file", proc.stderr)
                self.assertIn(error, proc.stderr)
                self.assertFalse(self.out.exists())
                if target.is_dir():
                    target.rmdir()
                else:
                    target.chmod(0o600)
                    target.unlink()
        if os.geteuid() == 0:
            print("skipped the permission-denied case: running as root",
                  file=sys.stderr)

    # -- review round 3: the launch-time guard ---------------------------

    def _fixture_copy(self, name="fixture-copy", **extra) -> Path:
        eval_dir = self.root / name
        shutil.copytree(EVAL_DIR, eval_dir)
        if extra:
            path = eval_dir / "fixture.yaml"
            fixture = yaml.safe_load(path.read_text(encoding="utf-8"))
            fixture.update(extra)
            path.write_text(yaml.safe_dump(fixture), encoding="utf-8")
        return eval_dir

    def test_symlinked_settings_in_a_seed_are_refused_before_anything_runs(self):
        hostile = self.root / "hostile-claude"
        hostile.mkdir()
        (hostile / "settings.json").write_text(
            json.dumps({"apiKeyHelper": "sentinel-helper"}), encoding="utf-8")
        tree = self.root / "hostile-tree"
        (tree / ".claude").mkdir(parents=True)
        (tree / ".claude" / "settings.local.json").write_text(
            json.dumps({"env": {"AWS_PROFILE": "x"}}), encoding="utf-8")
        registry = self._registry()
        for label, link, target in (("a .claude link", ".claude", hostile),
                                    ("a linked tree", "linked", tree)):
            with self.subTest(case=label):
                eval_dir = self._fixture_copy(f"fx-{link.strip('.')}")
                (eval_dir / "seed" / link).symlink_to(target,
                                                      target_is_directory=True)
                proc = self._run(str(eval_dir), "--trials", "1", "--registry",
                                 f"agentskills={registry}")
                self._assert_nothing_ran(proc)
                self.assertIn("settings", proc.stderr)
                self.assertNotIn("sentinel-helper", proc.stderr)
                self.assertFalse(self.out.exists())

    def test_a_symlink_loop_in_a_seed_does_not_hang_the_walk(self):
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        self.addCleanup(sys.path.remove, str(REPO_ROOT / "scripts"))
        import local_eval_guard
        seed = self.root / "loopy-seed"
        (seed / "a" / ".claude").mkdir(parents=True)
        (seed / "a" / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
        (seed / "a" / "back").symlink_to(seed, target_is_directory=True)
        (seed / "self").symlink_to(seed / "a", target_is_directory=True)
        found = local_eval_guard.settings_in_tree(seed)
        self.assertEqual([p.relative_to(seed).as_posix() for p in found],
                         ["a/.claude/settings.json"])

    def test_setup_that_writes_settings_is_caught_when_the_cli_launches(self):
        eval_dir = self._fixture_copy(
            setup="mkdir -p .claude && printf '%s' "
                  "'{\"apiKeyHelper\": \"sentinel-helper\"}' "
                  "> .claude/settings.json")
        proc = self._run(str(eval_dir), "--trials", "3", "--no-judge",
                         "--arm", "without_skill")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        roles = [c["role"] for c in self._calls()]
        self.assertNotIn("agent", roles, "the fake CLI must never be reached")
        self.assertIn("launch-time settings guard", proc.stderr)
        self.assertIn("trial 1", proc.stderr)
        self.assertIn("(flat)", proc.stderr)
        self.assertIn("apiKeyHelper", proc.stderr)
        self.assertIn("settings.json", proc.stderr)
        self.assertNotIn("sentinel-helper", proc.stderr)
        manifest = self._json("manifest.json")
        self.assertTrue(manifest["status"].startswith(
            "refused: the launch-time settings guard"))
        self.assertEqual([t["trial"] for t in manifest["trials"]], [1])
        self.assertFalse((self.out / "t2").exists(), "no further trial")
        self.assertFalse((self.out / "aggregate.json").exists())

    def test_a_credential_written_by_the_arm_fails_its_trial_and_the_next_launch(self):
        # The fake CLI plants user-level settings while the ARM runs (what a
        # fixture's setup or the agent could do). The arm's own check after
        # the turn fails trial 1 with `agent_wrote_agent_config` (the judge is
        # never launched), and the launcher's complete pre-flight refuses
        # trial 2's launch.
        target = self.home / ".claude" / "settings.json"
        self.assertIn(self.root, target.parents)
        proc = self._run(
            str(EVAL_DIR), "--trials", "2", "--arm", "without_skill",
            plant=(str(target), json.dumps({"apiKeyHelper": "sentinel-helper"})))
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        roles = [c["role"] for c in self._calls()]
        self.assertIn("agent", roles)
        self.assertNotIn("judge", roles, "the judge never reached the CLI")
        # The arm's own check sees the write first (the profile's settings
        # are watched after every turn, ADR 0011): trial 1 fails without a
        # judge call, and the launch guard refuses the next launch.
        (summary,) = self._summaries("without_skill", 1)
        self.assertEqual(summary["error"]["type"], "agent_wrote_agent_config")
        self.assertIn("trial 2", proc.stderr)
        self.assertIn(str(target), proc.stderr)
        self.assertIn("apiKeyHelper", proc.stderr)
        self.assertNotIn("sentinel-helper", proc.stderr)
        # Trial 2 was refused at its launch: it records an error, no score.
        for path in (self.out / "t2").rglob("summary.json"):
            self.assertIsNotNone(json.loads(path.read_text(encoding="utf-8"))["error"])

    def test_user_settings_written_by_setup_are_caught_at_the_next_launch(self):
        target = self.home / ".claude" / "settings.json"
        self.assertIn(self.root, target.parents, "the throwaway HOME")
        eval_dir = self._fixture_copy(
            setup='mkdir -p "$HOME/.claude" && printf \'%s\' '
                  '\'{"apiKeyHelper": "sentinel-helper"}\' '
                  '> "$HOME/.claude/settings.json"')
        proc = self._run(str(eval_dir), "--trials", "3", "--no-judge",
                         "--arm", "without_skill")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertTrue(target.is_file(), "the setup command ran")
        roles = [c["role"] for c in self._calls()]
        self.assertNotIn("agent", roles)
        self.assertIn("trial 1", proc.stderr)
        self.assertIn("(flat)", proc.stderr)
        self.assertIn(str(target), proc.stderr)
        self.assertNotIn("sentinel-helper", proc.stderr)
        self.assertFalse((self.out / "t2").exists())

    def test_every_child_kind_reaches_the_cli_through_the_guard(self):
        proc = self._run(str(EVAL_DIR), "--trials", "1",
                         "--registry", f"agentskills={self._registry()}")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        calls = self._calls()
        self.assertEqual({c["role"] for c in calls},
                         {"version", "probe", "agent", "judge"})
        for call in calls:
            with self.subTest(role=call["role"], n=call["n"]):
                guard_bin = Path(call["claude_bin"])
                self.assertEqual(guard_bin.name, "claude")
                self.assertTrue(guard_bin.parent.name.startswith("local-eval-guard-"))
                self.assertNotEqual(str(guard_bin), str(self.root / "claude-dispatch"))
                self.assertEqual(call["path_head"], str(guard_bin.parent))
        self.assertFalse(guard_bin.parent.exists(), "removed at exit")
        manifest_text = (self.out / "manifest.json").read_text(encoding="utf-8")
        self.assertNotIn("local-eval-guard-", manifest_text)
        manifest = json.loads(manifest_text)
        self.assertEqual(manifest["harness"]["claude_path"],
                         str(self.root / "claude-dispatch"))
        version = subprocess.run([sys.executable, str(FAKE_CLAUDE), "--version"],
                                 capture_output=True, text=True).stdout
        self.assertEqual(manifest["harness"]["claude_version"],
                         version.splitlines()[0].strip())

    def test_the_cli_receives_the_same_argv_with_and_without_the_guard(self):
        proc = self._run(str(EVAL_DIR), "--trials", "1", "--no-judge",
                         "--arm", "without_skill")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        guarded = [c["argv"] for c in self._calls() if c["role"] == "agent"]
        self.log.unlink()
        shutil.rmtree(self.state)
        self.state.mkdir()
        direct = subprocess.run(
            [sys.executable, str(REPO_ROOT / "harness" / "run_eval.py"),
             str(EVAL_DIR), "--arm", "without_skill", "--no-judge",
             "--results-dir", str(self.root / "direct-results")],
            capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=600,
            env=self._env(self._dispatcher()))
        self.assertEqual(direct.returncode, 0, direct.stdout + direct.stderr)
        unguarded = [c["argv"] for c in self._calls() if c["role"] == "agent"]
        self.assertEqual(len(guarded), 1)

        # Each run has its own mkdtemp workspace, which the arm's settings
        # spell out (its `.claude/` and session directory), so the settings
        # are compared by their workspace-independent part; nothing else in
        # argv may differ.
        def same_workspace(calls):
            out = []
            for argv in calls:
                argv = list(argv)
                index = argv.index("--settings")
                settings = json.loads(argv[index + 1])
                argv[index + 1] = json.dumps(
                    {"sandbox.network": settings["sandbox"]["network"],
                     "keys": sorted(settings)}, sort_keys=True)
                out.append([re.sub(r"workspace-[A-Za-z0-9_-]{8}", "workspace-<ws>", arg)
                            for arg in argv])
            return out
        self.assertEqual(same_workspace(guarded), same_workspace(unguarded))

    def test_proxy_userinfo_is_read_from_the_url_not_from_any_at_sign(self):
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        self.addCleanup(sys.path.remove, str(REPO_ROOT / "scripts"))
        import local_eval
        for value in ("http://h.example.com:80?x=a@b", "http://h.example.com/p#a@b",
                      "http://h.example.com:3128", "h.example.com:3128"):
            with self.subTest(value=value):
                self.assertEqual(
                    local_eval.proxy_userinfo_names({"HTTPS_PROXY": value}), [])
        for value in ("http://u:p@h.example.com:80", "http://u@h.example.com",
                      "u:p@h.example.com:3128"):
            with self.subTest(value=value):
                self.assertEqual(
                    local_eval.proxy_userinfo_names({"HTTPS_PROXY": value}),
                    ["HTTPS_PROXY"])

    def test_a_tool_toggle_in_the_use_family_is_not_a_provider(self):
        registry = self._registry()
        proc = self._run(str(EVAL_DIR), "--trials", "1", "--no-judge",
                         "--registry", f"agentskills={registry}",
                         env_extra={"CLAUDE_CODE_USE_POWERSHELL_TOOL": "1"})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self._settings({"env": {"CLAUDE_CODE_USE_POWERSHELL_TOOL": "1"}})
        proc = self._run(str(EVAL_DIR), "--trials", "1", "--no-judge",
                         "--registry", f"agentskills={registry}",
                         results_dir=self.root / "second")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_a_symlinked_results_dir_is_still_a_local_exhibit_to_make_badge(self):
        proc = self._run(str(EVAL_DIR), "--trials", "1", "--no-judge",
                         "--registry", f"agentskills={self._registry()}")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self._unstamp_summaries()
        link = self.root / "innocent-looking"
        # Only a PARENT of the target carries the marker, so the link's own
        # path shows none: refusing needs the real path.
        link.symlink_to(self.out / "t1" / SKILL, target_is_directory=True)
        badge_proc, badge = self._make_badge(SKILL, link)
        self.assertNotEqual(badge_proc.returncode, 0)
        self.assertIn("inside a local exhibit", badge_proc.stderr)
        self.assertFalse(badge.exists())

    # -- review round 4: the launcher as a unit --------------------------

    def _launcher(self):
        """The generated guard launcher, with a fake 'real CLI' that records
        its argv. Returns (launcher, real_cli_log, refusal_record)."""
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        self.addCleanup(sys.path.remove, str(REPO_ROOT / "scripts"))
        import local_eval_guard
        unit = self.root / "launcher-unit"
        unit.mkdir()
        log, record = unit / "real-cli-calls.jsonl", unit / "refusals.jsonl"
        real = unit / "real-claude"
        real.write_text(
            f"#!{sys.executable}\nimport json, sys\n"
            f"open({str(log)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n",
            encoding="utf-8")
        real.chmod(0o700)
        launcher = unit / "claude"
        launcher.write_text(local_eval_guard.launcher_source(
            python=sys.executable, scripts_dir=str(REPO_ROOT / "scripts"),
            real_cli=str(real), repo_root=str(unit / "harness-checkout"),
            record=str(record)), encoding="utf-8")
        launcher.chmod(0o700)
        return launcher, log, record

    def _launch(self, launcher, cwd, *args):
        return subprocess.run(
            [str(launcher), *args], cwd=str(cwd), capture_output=True, text=True,
            env={"PATH": "/usr/bin:/bin", "HOME": str(self.home)}, timeout=60)

    def _real_calls(self, log):
        if not log.exists():
            return []
        return [json.loads(line) for line in
                log.read_text(encoding="utf-8").splitlines()]

    def _hostile(self, directory, name="settings.json"):
        (directory / ".claude").mkdir(parents=True, exist_ok=True)
        (directory / ".claude" / name).write_text(
            json.dumps({"apiKeyHelper": "sentinel-helper"}), encoding="utf-8")

    def test_the_launcher_refuses_without_starting_the_cli(self):
        launcher, log, record = self._launcher()
        cwd = self.root / "ws"
        self._hostile(cwd)
        proc = self._launch(launcher, cwd, "-p", "x")
        self.assertEqual(proc.returncode, 87, proc.stderr)
        self.assertEqual(self._real_calls(log), [], "the real CLI must not start")
        self.assertIn(str(cwd / ".claude" / "settings.json"), proc.stderr)
        self.assertIn("apiKeyHelper", proc.stderr)
        self.assertNotIn("sentinel-helper", proc.stderr)
        self.assertIn("settings.json", record.read_text(encoding="utf-8"))

    def test_the_launcher_hands_a_clean_launch_to_the_cli_with_isolation_flags(self):
        # A print-mode session gets --strict-mcp-config and, when it names no
        # setting source, `--setting-sources project` (skill-creator's own
        # `claude -p` names none); never --no-session-persistence, since a
        # caller may resume. Each flag only when absent; other argv unchanged.
        launcher, log, _ = self._launcher()
        cwd = self.root / "clean-ws"
        cwd.mkdir()
        cases = (
            (["-p", "a prompt", "--output-format", "json", "--model", "m"],
             ["--strict-mcp-config", "--setting-sources", "project",
              "-p", "a prompt", "--output-format", "json", "--model", "m"]),
            (["-p", "--setting-sources", "", "--model", "m"],
             ["--strict-mcp-config", "-p", "--setting-sources", "", "--model", "m"]),
            (["--print", "x", "--setting-sources=user", "--strict-mcp-config"],
             ["--print", "x", "--setting-sources=user", "--strict-mcp-config"]),
            (["plugin", "list"], ["plugin", "list"]),
        )
        for argv, _ in cases:
            proc = self._launch(launcher, cwd, *argv)
            self.assertEqual(proc.returncode, 0, proc.stderr)
        calls = self._real_calls(log)
        self.assertEqual(calls, [want for _, want in cases])
        for call in calls:
            self.assertNotIn("--no-session-persistence", call)

    def test_only_exactly_a_bare_version_skips_the_settings_check(self):
        launcher, log, _ = self._launcher()
        cwd = self.root / "ws"
        self._hostile(cwd)
        proc = self._launch(launcher, cwd, "--version")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._real_calls(log), [["--version"]])
        for args in (("--version", "--print", "x"), ("-v",), ("--VERSION",), ()):
            with self.subTest(args=args):
                proc = self._launch(launcher, cwd, *args)
                self.assertEqual(proc.returncode, 87, proc.stderr)
        self.assertEqual(len(self._real_calls(log)), 1, "only the bare one ran")

    def test_the_launcher_checks_every_parent_up_to_the_filesystem_root(self):
        launcher, log, _ = self._launcher()
        project = self.root / "project"
        for label, cwd in (("one level up", project / "sub"),
                           ("above a git root", project / "sub" / "repo")):
            with self.subTest(case=label):
                cwd.mkdir(parents=True, exist_ok=True)
                (cwd / ".git").mkdir(exist_ok=True)
                self._hostile(project, "settings.local.json")
                proc = self._launch(launcher, cwd, "-p", "x")
                self.assertEqual(proc.returncode, 87, proc.stderr)
                self.assertIn(str(project / ".claude" / "settings.local.json"),
                              proc.stderr)
        self.assertEqual(self._real_calls(log), [])

    def test_the_launcher_checks_beneath_the_cwd(self):
        launcher, log, _ = self._launcher()
        cwd = self.root / "ws"
        self._hostile(cwd / "a" / "deeper")
        proc = self._launch(launcher, cwd, "-p", "x")
        self.assertEqual(proc.returncode, 87, proc.stderr)
        self.assertIn(str(cwd / "a" / "deeper" / ".claude" / "settings.json"),
                      proc.stderr)
        self.assertEqual(self._real_calls(log), [])

    def test_an_unlistable_claude_directory_is_a_refusal(self):
        if os.geteuid() == 0:
            self.skipTest("running as root: a permission-denied listing "
                          "cannot be arranged")
        launcher, log, _ = self._launcher()
        parent = self.root / "project"
        (parent / ".claude").mkdir(parents=True)
        cwd = parent / "sub"
        cwd.mkdir()
        (parent / ".claude").chmod(0)
        self.addCleanup((parent / ".claude").chmod, 0o700)
        proc = self._launch(launcher, cwd, "-p", "x")
        self.assertEqual(proc.returncode, 87, proc.stderr)
        self.assertIn("cannot list", proc.stderr)
        self.assertEqual(self._real_calls(log), [])

    def test_the_provider_exemption_is_one_exact_name(self):
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        self.addCleanup(sys.path.remove, str(REPO_ROOT / "scripts"))
        import local_eval_guard
        names = {"CLAUDE_CODE_USE_POWERSHELL_TOOL": "1",
                 "CLAUDE_CODE_USE_POWERSHELL_TOOL_X": "1",
                 "CLAUDE_CODE_USE_POWERSHELL": "1",
                 "CLAUDE_CODE_USE_BEDROCK": "1"}
        self.assertEqual(
            local_eval_guard.refused_env_names(names),
            ["CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_POWERSHELL",
             "CLAUDE_CODE_USE_POWERSHELL_TOOL_X"])


if __name__ == "__main__":
    unittest.main()
