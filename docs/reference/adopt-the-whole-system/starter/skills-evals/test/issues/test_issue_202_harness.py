#!/usr/bin/env python3
"""Issue #202, the harness amendment (owner's decision, 2026-09-27):
"Removing cool off from haiku and preflight. ... Can we just use what is
already in the environment — or if there is none, download latest — and
record the harness and model versions used?"

Three pieces, each tested here:

  * `evals/roster-policy.yml` ships `cooling_off_days: 0`, and the roster's
    reasons read sensibly at 0 (no "past the 0-day cooling-off").
  * `.github/workflows/eval.yml` and `propagation.yml` ALWAYS install the
    npm latest (#203, 2026-09-28) — never a preinstalled CLI, before any
    credential — refuse an unusable npm answer or a shadowing `claude`, and
    record the version in the step summary. Parsed with `yaml`, never matched
    out of the file as text, and the body is executed against a fake `npm`
    and `claude`.
  * `harness/run_eval.py` records `harness` and `models_used` (and the
    judge's `judge_models_used`) in every arm's summary.json and names them in
    report.md; `harness/run_propagation.py`'s `--json` record carries each
    arm's `harness_version` and init-event `model`.

Hermetic, like the rest of the suite: every CLI call goes to test/fake-claude
(or test/fake-claude-init) through $CLAUDE_BIN, and every roster call runs on
a frozen `now`.

Discovered and run by test/run_tests.py; also runnable on its own with
`python3 test/issues/test_issue_202_harness.py`, which really runs it — the
`unittest.main()` at the bottom is what makes that true.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import yaml

TEST_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = TEST_DIR.parent
HARNESS_DIR = REPO_ROOT / "harness"
POLICY = REPO_ROOT / "evals" / "roster-policy.yml"
EVAL_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "eval.yml"
PROPAGATION_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "propagation.yml"
FAKE_CLAUDE = TEST_DIR / "fake-claude"
# A nested fixture: its arms are written under its own name (#66).
EVAL_DIR = REPO_ROOT / "evals" / "workflow-path-audit" / "required-check-routing"
FAKE_VERSION_LINE = "fake-claude 0.0.0 (hermetic test stub)"

sys.path.insert(0, str(HARNESS_DIR))
import roster  # noqa: E402
import run_eval  # noqa: E402
import run_propagation  # noqa: E402
from propagation import arms, init_probe  # noqa: E402


# ---------------------------------------------------------------------------
# 1. model cooling-off -> 0


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from arm_test_env import install_arm_test_environment  # noqa: E402


@install_arm_test_environment
def setUpModule() -> None:
    pass


def tearDownModule() -> None:
    unittest.doModuleCleanups()


class TestShippedCoolingOff(unittest.TestCase):

    def test_the_shipped_policy_sets_zero_and_says_why(self):
        policy = roster.load_policy(POLICY)
        self.assertEqual(policy["cooling_off_days"], 0)
        roster.validate_policy(policy)
        text = POLICY.read_text(encoding="utf-8")
        self.assertIn("2026-09-27", text)


class TestZeroCoolingOffWording(unittest.TestCase):
    """At 0 days every model is past the cooling-off; the reasons must not
    say "past the 0-day cooling-off"."""

    NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)

    @staticmethod
    def _models():
        return {"fetched_at": "2026-09-27T11:00:00Z", "models": [
            {"id": "claude-haiku-4-5", "display_name": "Claude Haiku 4.5",
             "created_at": "2025-10-01T00:00:00Z"},
            {"id": "claude-haiku-5", "display_name": "Claude Haiku 5",
             "created_at": "2026-09-27T06:00:00Z"},
            {"id": "claude-opus-5", "display_name": "Claude Opus 5",
             "created_at": "2026-04-01T00:00:00Z"},
        ]}

    def _compute(self, days):
        policy = roster.load_policy(POLICY)
        policy["cooling_off_days"] = days
        warnings: list[str] = []
        # No census: every tier's newest is seated, which exercises the
        # newest-per-tier wording as well as the preflight's.
        result = roster.compute_roster(
            models_doc=self._models(),
            census_doc={"generated_at": None, "counts": {}},
            policy=policy, previous=None, now=self.NOW, warn=warnings.append)
        return result

    def test_the_preflight_picks_the_newest_in_the_lowest_tier_at_zero(self):
        result = self._compute(0)
        self.assertEqual(result["preflight"]["id"], "claude-haiku-5")
        reason = result["preflight"]["reason"]
        self.assertNotIn("0-day", reason)
        self.assertIn("no cooling-off", reason)

    def test_the_newest_in_tier_reason_reads_correctly_at_zero(self):
        result = self._compute(0)
        reason = next(a["reason"] for a in result["arms"]
                      if a["id"] == "claude-haiku-5")
        self.assertNotIn("0-day", reason)
        self.assertIn("no cooling-off", reason)

    def test_a_positive_cooling_off_keeps_its_wording(self):
        result = self._compute(7)
        self.assertEqual(result["preflight"]["id"], "claude-haiku-4-5")
        self.assertIn("7-day cooling-off", result["preflight"]["reason"])
        excluded = next(e["reason"] for e in result["excluded"]
                        if e["id"] == "claude-haiku-5")
        self.assertIn("inside the 7-day cooling-off", excluded)


# ---------------------------------------------------------------------------
# 2. the harness install: always the npm latest; recorded


class TestHarnessInstallStep(unittest.TestCase):
    """Both workflows ALWAYS install the npm latest (#203, the owner's
    decision of 2026-09-28): never a preinstalled CLI, never a pin, and a
    `claude` earlier on PATH that shadows the install fails the step."""

    PIN = re.compile(r"claude-code@\d")
    BAD_LATEST = "::error::npm reported no usable latest Claude Code version"
    SHADOWED = "::error::claude on PATH is not the npm latest just installed"

    @staticmethod
    def _steps(path, job):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        return doc["jobs"][job]["steps"]

    @staticmethod
    def _install_index(steps):
        return next(i for i, s in enumerate(steps)
                    if s.get("name") == "Install Claude Code CLI")

    @staticmethod
    def _references_a_secret(step):
        return "secrets." in json.dumps(step)

    def _bodies(self):
        return {"eval": self._steps(EVAL_WORKFLOW, "eval"),
                "propagation": self._steps(PROPAGATION_WORKFLOW, "arms")}

    def _body(self, which):
        steps = self._bodies()[which]
        return steps[self._install_index(steps)]["run"]

    def _assert_install_shape(self, script):
        self.assertIsNone(self.PIN.search(script),
                          "the Claude Code install is pinned again")
        self.assertNotIn("${{", script)
        self.assertNotIn("command -v claude", script,
                         "a preinstalled CLI must never be reused")
        self.assertNotIn("preinstalled", script)
        self.assertNotIn("@latest", script)
        lines = [ln.strip() for ln in script.splitlines()]
        installs = [ln for ln in lines
                    if "npm install" in ln and "@anthropic-ai/claude-code" in ln]
        self.assertEqual(installs, ['npm install -g "@anthropic-ai/claude-code@${latest}"'])
        self.assertIn('latest="$(npm view @anthropic-ai/claude-code version)" || '
                      '{ echo "::error::could not resolve the latest Claude Code '
                      'version"; exit 1; }', lines)
        self.assertIn('[[ "$latest" =~ ^[0-9]+\\.[0-9]+\\.[0-9]+$ ]] || '
                      f'{{ echo "{self.BAD_LATEST}"; exit 1; }}', lines)
        self.assertNotIn("${latest//", script, "junk must be refused, not stripped")
        self.assertIn('[[ "${version%% *}" == "$latest" ]] || '
                      f'{{ echo "{self.SHADOWED}"; exit 1; }}', lines)
        self.assertLess(lines.index(installs[0]),
                        next(i for i, ln in enumerate(lines)
                             if ln.startswith('[[ "${version%% *}"')))
        self.assertNotIn("| head", script,
                         "a pipe into head under pipefail can fail the step")
        self.assertIn("GITHUB_STEP_SUMMARY", script)
        self.assertIn("set -euo pipefail", script)
        # `-k 10`: a CLI that ignores SIGTERM is killed 10 s later (#203
        # round 1), the exact line rather than a substring.
        self.assertIn('version="$(timeout -k 10 60 claude --version)" || '
                      '{ echo "::error::claude --version failed"; exit 1; }', lines)
        self.assertIn('version="${version:0:80}"', script)
        self.assertIn('version="${version//[^A-Za-z0-9._() -]/}"', script)
        self.assertIn('[[ -n "$version" ]] || {', script)
        self.assertEqual(script.count("::error::"), 5)

    def test_both_bodies_have_the_always_latest_shape_and_match(self):
        for which in ("eval", "propagation"):
            with self.subTest(workflow=which):
                self._assert_install_shape(self._body(which))
        self.assertEqual(self._body("eval"), self._body("propagation"))

    def test_eval_installs_before_any_credential(self):
        steps = self._steps(EVAL_WORKFLOW, "eval")
        at = self._install_index(steps)
        mint = next(i for i, s in enumerate(steps)
                    if "token" in (s.get("name") or "").lower()
                    and "exchange" in (s.get("name") or "").lower())
        first_secret = next((i for i, s in enumerate(steps)
                             if self._references_a_secret(s)), len(steps))
        self.assertLess(at, mint)
        self.assertLess(at, first_secret)

    def test_propagation_installs_before_the_probe_with_no_secret_ahead(self):
        steps = self._steps(PROPAGATION_WORKFLOW, "arms")
        at = self._install_index(steps)
        probe = next(i for i, s in enumerate(steps)
                     if s.get("name") == "Probe the arm")
        self.assertLess(at, probe)
        self.assertFalse(any(self._references_a_secret(s) for s in steps[:at + 1]))

    # --- the body, executed against a fake npm and a fake claude ----------

    def _tool(self, path, body):
        path.write_text("#!/bin/bash\n" + body, encoding="utf-8")
        path.chmod(0o755)

    def _run(self, which, *, view="printf '2.1.290\\n'", view_rc=0, install_rc=0,
             installed_version="2.1.290 (Claude Code)", shadow_version=None):
        """Run one workflow's install body with PATH = [shadow] + fakes + the
        coreutils it needs. The fake `npm install` writes the `claude` it
        "installs" into the fakes dir; a `shadow` dir ahead of it on PATH
        holds a stale one."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        fakes, tools, shadow = tmp / "fakes", tmp / "tools", tmp / "shadow"
        for d in (fakes, tools, shadow):
            d.mkdir()
        for tool in ("timeout", "cp"):
            (tools / tool).symlink_to(shutil.which(tool))
        calls = tmp / "npm-calls.txt"
        # What `npm install` puts on PATH: a claude whose `--version` has a
        # second line, which the step must drop.
        template = tmp / "claude.template"
        self._tool(template, f"printf '%s\\nsecond line\\n' "
                             f"{shlex.quote(installed_version)}\n")
        self._tool(fakes / "npm", (
            f'echo "$*" >> {shlex.quote(str(calls))}\n'
            'if [ "$1" = view ]; then\n'
            f'  {view}\n  exit {view_rc}\n'
            'fi\n'
            'if [ "$1" = install ]; then\n'
            f'  [ {install_rc} -eq 0 ] || exit {install_rc}\n'
            f'  cp {shlex.quote(str(template))} {shlex.quote(str(fakes / "claude"))}\n'
            'fi\n'))
        if shadow_version is not None:
            self._tool(shadow / "claude",
                       f"printf '%s\\n' {shlex.quote(shadow_version)}\n")
        summary = tmp / "summary.md"
        path = os.pathsep.join(str(d) for d in (shadow, fakes, tools))
        done = subprocess.run(
            [shutil.which("bash"), "-c", self._body(which)], capture_output=True,
            text=True, timeout=60,
            env={"PATH": path, "GITHUB_STEP_SUMMARY": str(summary)})
        npm_calls = (calls.read_text(encoding="utf-8").splitlines()
                     if calls.is_file() else [])
        return done, (summary.read_text(encoding="utf-8")
                      if summary.is_file() else ""), npm_calls

    def _each(self):
        for which in ("eval", "propagation"):
            with self.subTest(workflow=which):
                yield which

    @unittest.skipUnless(shutil.which("bash") and shutil.which("timeout"),
                         "needs bash and timeout")
    def test_the_happy_path_installs_exactly_the_npm_latest(self):
        for which in self._each():
            done, summary, npm = self._run(which)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            self.assertEqual(npm, ["view @anthropic-ai/claude-code version",
                                   "install -g @anthropic-ai/claude-code@2.1.290"])
            self.assertIn("harness: Claude Code 2.1.290 (Claude Code) (npm latest)",
                          done.stdout)
            # A multi-line `--version` keeps line 1 only.
            self.assertNotIn("second line", done.stdout + summary)
            self.assertEqual(summary, "### Harness\nClaude Code "
                                      "`2.1.290 (Claude Code)` (npm latest)\n")

    @unittest.skipUnless(shutil.which("bash") and shutil.which("timeout"),
                         "needs bash and timeout")
    def test_a_failing_npm_view_fails_the_step_by_name(self):
        for which in self._each():
            done, _, npm = self._run(which, view="echo 'E404' >&2", view_rc=1)
            self.assertNotEqual(done.returncode, 0)
            self.assertIn("::error::could not resolve the latest Claude Code version",
                          done.stdout)
            self.assertFalse([c for c in npm if c.startswith("install")], npm)

    @unittest.skipUnless(shutil.which("bash") and shutil.which("timeout"),
                         "needs bash and timeout")
    def test_an_unusable_npm_answer_is_refused_not_stripped(self):
        for answer in ("printf '2.2.0-beta.1\\n'", "printf '<html>\\n'", "true",
                       "printf '2.1.3\\n2.1.4\\n'", "printf 'v2.1.3\\n'",
                       "printf '2.1\\n'"):
            for which in self._each():
                with self.subTest(answer=answer):
                    done, _, npm = self._run(which, view=answer)
                    self.assertNotEqual(done.returncode, 0)
                    self.assertIn(self.BAD_LATEST, done.stdout.splitlines())
                    self.assertFalse([c for c in npm if c.startswith("install")], npm)

    @unittest.skipUnless(shutil.which("bash") and shutil.which("timeout"),
                         "needs bash and timeout")
    def test_a_failing_install_fails_the_step(self):
        for which in self._each():
            done, summary, _ = self._run(which, install_rc=3)
            self.assertNotEqual(done.returncode, 0)
            self.assertEqual(summary, "")

    @unittest.skipUnless(shutil.which("bash") and shutil.which("timeout"),
                         "needs bash and timeout")
    def test_a_shadowing_claude_fails_the_step(self):
        for shadow in ("2.1.200 (Claude Code)", "2.1.2900 (Claude Code)"):
            for which in self._each():
                with self.subTest(shadow=shadow):
                    done, summary, _ = self._run(which, shadow_version=shadow)
                    self.assertNotEqual(done.returncode, 0)
                    self.assertIn(self.SHADOWED, done.stdout)
                    self.assertEqual(summary, "")

    @unittest.skipUnless(shutil.which("bash") and shutil.which("timeout"),
                         "needs bash and timeout")
    def test_a_prefix_is_not_a_match(self):
        # adam-agentskills#29: a stale 2.1.30 passed a prefix check for 2.1.3.
        for which in self._each():
            done, _, _ = self._run(which, view="printf '2.1.3\\n'",
                                   installed_version="2.1.3 (Claude Code)",
                                   shadow_version="2.1.30 (Claude Code)")
            self.assertNotEqual(done.returncode, 0)
            self.assertIn(self.SHADOWED, done.stdout)
            ok, _, _ = self._run(which, view="printf '2.1.3\\n'",
                                 installed_version="2.1.3 (Claude Code)")
            self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)


# ---------------------------------------------------------------------------
# 3. run_eval records the harness and the models


def _write_script(path: Path, body: str) -> Path:
    path.write_text(f"#!/usr/bin/env python3\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return path


class TestClaudeVersion(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _version(self, binary):
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"CLAUDE_BIN": str(binary)}), \
             contextlib.redirect_stderr(err):
            return run_eval.claude_version(), err.getvalue()

    def test_the_fake_cli_version_is_read(self):
        version, err = self._version(FAKE_CLAUDE)
        self.assertEqual(version, FAKE_VERSION_LINE)
        self.assertEqual(err, "")

    def test_first_line_only_stripped_and_capped(self):
        script = _write_script(self.tmp / "cli",
                               "print('  ' + 'v' * 200 + '  ')\nprint('second')")
        version, _ = self._version(script)
        self.assertEqual(version, "v" * 64)

    def test_empty_output_is_null_and_a_warning(self):
        for body in ("pass", "print('')", "print('\\n2.1.0')", "print('`[]<>')"):
            with self.subTest(body=body):
                version, err = self._version(
                    _write_script(self.tmp / "cli", body))
                self.assertIsNone(version)
                self.assertIn("no version", err)

    def test_a_markdown_payload_is_reduced_to_version_characters(self):
        # The version lands in a markdown code span (report.md, the step
        # summary): a backtick or a link must not survive.
        script = _write_script(
            self.tmp / "cli",
            "print('1.0 `[x](http://example.com)` <b>\\r')\nprint('2')")
        version, _ = self._version(script)
        self.assertEqual(version, "1.0 x(httpexample.com) b")

    def test_crlf_is_stripped(self):
        script = _write_script(self.tmp / "cli",
                               "import sys\nsys.stdout.write('2.1.300 (Claude Code)\\r\\n')")
        version, _ = self._version(script)
        self.assertEqual(version, "2.1.300 (Claude Code)")

    def test_a_missing_binary_is_null_and_a_classname_warning(self):
        version, err = self._version(self.tmp / "no-such-claude")
        self.assertIsNone(version)
        self.assertIn("FileNotFoundError", err)
        self.assertNotIn(str(self.tmp), err)

    def test_a_failing_binary_is_null_and_a_status_warning(self):
        script = _write_script(self.tmp / "cli",
                               "import sys\nprint('secret-ish text')\nsys.exit(3)")
        version, err = self._version(script)
        self.assertIsNone(version)
        self.assertIn("exit 3", err)
        self.assertNotIn("secret-ish", err)


class TestModelsUsed(unittest.TestCase):

    def test_sorted_keys_of_model_usage(self):
        self.assertEqual(run_eval.models_used(
            {"modelUsage": {"model-b": {}, "model-a": {"x": 1}}}),
            ["model-a", "model-b"])

    def test_absent_or_junk_is_empty(self):
        for raw in (None, {}, {"modelUsage": None}, {"modelUsage": []},
                    {"modelUsage": "model-a"}, [], "text"):
            with self.subTest(raw=raw):
                self.assertEqual(run_eval.models_used(raw), [])

    def test_overlong_names_are_ignored_and_the_list_is_capped(self):
        usage = {f"model-{i:02d}": {} for i in range(40)}
        usage["x" * 129] = {}
        usage[""] = {}
        got = run_eval.models_used({"modelUsage": usage})
        self.assertEqual(len(got), 16)
        self.assertEqual(got, sorted(got))
        self.assertNotIn("x" * 129, got)
        self.assertNotIn("", got)

    def test_ids_that_would_break_the_report_line_are_ignored(self):
        usage = {"model-a": {}, "model-b[1m]": {}, "bad\nid": {}, "bad id": {},
                 "bad`id": {}, "bad|id": {}, "bad\u0000id": {}}
        self.assertEqual(run_eval.models_used({"modelUsage": usage}),
                         ["model-a", "model-b[1m]"])


class TestFakeCliCarriesModelUsage(unittest.TestCase):

    def test_the_agent_result_names_the_model_it_was_asked_for(self):
        env = dict(os.environ, FAKE_CLAUDE_MODE="agent")
        out = subprocess.run([str(FAKE_CLAUDE), "-p", "x", "--output-format",
                              "json", "--model", "fake-model-a"],
                             capture_output=True, text=True, env=env, timeout=60,
                             check=True)
        self.assertEqual(list(json.loads(out.stdout)["modelUsage"]),
                         ["fake-model-a"])


class _RunEvalEndToEnd(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.results = self.tmp / "results"

    def _registry(self, skill):
        skill_dir = self.tmp / "registry" / "plugins" / "b" / "skills" / skill
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {skill}\ndescription: stand-in.\n---\n", encoding="utf-8")
        return self.tmp / "registry"

    def _run(self, eval_dir, *extra, mode="agent_and_judge", env_extra=None):
        env = os.environ.copy()
        env.update({"CLAUDE_BIN": str(FAKE_CLAUDE), "FAKE_CLAUDE_MODE": mode})
        env.update(env_extra or {})
        cmd = [sys.executable, str(HARNESS_DIR / "run_eval.py"), str(eval_dir),
               "--results-dir", str(self.results), "--timeout", "30", *extra]
        return subprocess.run(cmd, capture_output=True, text=True, env=env,
                              cwd=str(REPO_ROOT), timeout=300)

    def _run_dir(self, key):
        runs = list((self.results / key).iterdir())
        self.assertEqual(len(runs), 1)
        return runs[0]


def _roster_models():
    """The models an unpinned fixture runs on: the committed roster's first
    arm and the judges it may get, a tier above (#371, ADR 0013): every roster
    arm in the lowest tier above it, else the roster's judge. EVAL_DIR pins
    neither (#371 item 5). Worked out here from the ladder, not by
    `judge_policy.judge_for_arm`; which of several arms is the newest is tested
    in test/issues/test_issue_371_judge_tier.py."""
    committed = yaml.safe_load((REPO_ROOT / "evals" / "roster.yml").read_text(encoding="utf-8"))
    arm_ids = [arm["id"] for arm in committed["arms"]]
    rungs = roster.tier_rungs(roster.load_policy(
        REPO_ROOT / "evals" / "roster-policy.yml"))
    tier = roster.rung_of(arm_ids[0], rungs)
    placed = [(roster.rung_of(arm, rungs), arm) for arm in arm_ids]
    above = sorted((rung, arm) for rung, arm in placed
                   if rung is not None and rung > tier)
    lowest = [arm for rung, arm in above if rung == above[0][0]]
    judges = lowest or [committed["judge"]["id"]]
    for judge in judges:
        assert roster.rung_of(judge, rungs) > tier, (arm_ids[0], judge)
    return arm_ids[0], judges


class TestSkillArmsRecordVersions(_RunEvalEndToEnd):

    def test_both_arms_record_harness_and_models(self):
        skill = run_eval.load_fixture(EVAL_DIR)["skill"]
        pinned, judges = _roster_models()
        proc = self._run(EVAL_DIR, "--arm", "both",
                         env_extra={"AGENTSKILLS_DIR": str(self._registry(skill))})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        run_dir = self._run_dir(skill)
        for arm in ("with_skill", "without_skill"):
            with self.subTest(arm=arm):
                summary = json.loads((run_dir / EVAL_DIR.name / arm / "summary.json")
                                     .read_text(encoding="utf-8"))
                self.assertEqual(summary["harness"],
                                 {"name": "claude-code",
                                  "version": FAKE_VERSION_LINE,
                                  "permission_mode": "auto", "effort": None})
                self.assertEqual(summary["models_used"], [pinned])
                self.assertEqual(len(summary["judge_models_used"]), 1)
                self.assertIn(summary["judge_models_used"][0], judges)
        report = (run_dir / "report.md").read_text(encoding="utf-8")
        harness_lines = [ln for ln in report.splitlines()
                         if ln.startswith("- Harness:")]
        self.assertEqual(len(harness_lines), 1, report)
        self.assertIn(FAKE_VERSION_LINE, harness_lines[0])
        self.assertIn(f"with_skill={pinned}", harness_lines[0])
        self.assertIn(f"without_skill={pinned}", harness_lines[0])

    def test_an_arm_that_errors_still_records_the_harness(self):
        # A CLI that answers --version and fails every agent call. (A
        # FAKE_CLAUDE_MODE would not reach the agent call: agent_env's
        # allowlist scrubs it.)
        skill = run_eval.load_fixture(EVAL_DIR)["skill"]
        wrapper = _write_script(self.tmp / "cli", (
            "import sys\n"
            "if '--version' in sys.argv:\n"
            f"    print({FAKE_VERSION_LINE!r})\n"
            "    sys.exit(0)\n"
            "sys.exit(1)"))
        proc = self._run(EVAL_DIR, "--arm", "without_skill",
                         env_extra={"CLAUDE_BIN": str(wrapper)})
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        summary = json.loads((self._run_dir(skill) / EVAL_DIR.name / "without_skill" /
                              "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["error"]["type"], "nonzero_exit")
        self.assertEqual(summary["harness"]["version"], FAKE_VERSION_LINE)
        self.assertEqual(summary["models_used"], [])

    def test_a_setup_failure_still_records_the_harness(self):
        eval_dir = self.tmp / "evals" / "setup-fails"
        (eval_dir / "seed").mkdir(parents=True)
        (eval_dir / "seed" / "README.md").write_text("x\n", encoding="utf-8")
        (eval_dir / "fixture.yaml").write_text(yaml.safe_dump({
            "skill": "setup-fails", "prompt": "do it", "model": "fake-model-a",
            "judge_rubric": "r", "setup": "exit 7",
            "objective_checks": []}), encoding="utf-8")
        proc = self._run(eval_dir, "--arm", "without_skill", "--no-judge")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        summary = json.loads((self._run_dir("setup-fails") / "without_skill" /
                              "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["error"]["type"], "setup_failed")
        self.assertEqual(summary["harness"],
                         {"name": "claude-code", "version": FAKE_VERSION_LINE,
                          "permission_mode": "auto", "effort": None})
        self.assertEqual(summary["models_used"], [])

    def test_an_unreadable_version_is_null_and_the_run_goes_on(self):
        skill = run_eval.load_fixture(EVAL_DIR)["skill"]
        wrapper = _write_script(self.tmp / "cli", (
            "import os, sys\n"
            "if '--version' in sys.argv:\n"
            "    sys.exit(9)\n"
            f"os.execv({str(FAKE_CLAUDE)!r}, [{str(FAKE_CLAUDE)!r}] + sys.argv[1:])"))
        proc = self._run(EVAL_DIR, "--arm", "without_skill", "--no-judge",
                         env_extra={"CLAUDE_BIN": str(wrapper)})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("exit 9", proc.stderr)
        summary = json.loads((self._run_dir(skill) / EVAL_DIR.name / "without_skill" /
                              "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["harness"], {"name": "claude-code", "version": None,
                                              "permission_mode": "auto", "effort": None})
        self.assertIsNone(summary["error"])
        self.assertEqual(summary["judge_models_used"], [])


class TestJudgeModelsOnTheExceptionPath(_RunEvalEndToEnd):
    """F8 (#203 round 1): a judge CLI call that completed is recorded even
    when `judge.score` then raises on its answer."""

    def test_a_judge_that_answers_junk_still_records_its_model(self):
        skill = run_eval.load_fixture(EVAL_DIR)["skill"]
        _, judges = _roster_models()
        # Prose for every call: a fine transcript for the agent, and an
        # answer judge.score cannot parse, AFTER the CLI call completed.
        wrapper = _write_script(self.tmp / "cli", (
            "import os, sys\n"
            "os.environ['FAKE_CLAUDE_MODE'] = 'agent'\n"
            f"os.execv({str(FAKE_CLAUDE)!r}, [{str(FAKE_CLAUDE)!r}] + sys.argv[1:])"))
        proc = self._run(EVAL_DIR, "--arm", "without_skill",
                         env_extra={"CLAUDE_BIN": str(wrapper)})
        summary = json.loads((self._run_dir(skill) / EVAL_DIR.name / "without_skill" /
                              "summary.json").read_text(encoding="utf-8"))
        self.assertIn("error", summary["judge"] or {}, proc.stdout + proc.stderr)
        self.assertEqual(len(summary["judge_models_used"]), 1)
        self.assertIn(summary["judge_models_used"][0], judges)


class TestGuidanceArmsRecordVersions(unittest.TestCase):
    """The guidance path's `_write_summary` call sites, driven with the
    delivery, guard and agent patched so no hook or checkout is needed."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.args = types.SimpleNamespace(
            results_dir=self.tmp / "results", model="fake-model-a",
            timeout=30, no_judge=True, harness_version="9.9.9 (Claude Code)")
        self.ctx = {"delivery": "user", "decoys": {}, "token": "TOK",
                    "guidance_dir": self.tmp, "row": {}, "section": "s",
                    "key": "guidance/s"}
        self.arm = {"name": "treatment", "mode": "section",
                    "objective_checks": []}
        self.fixture = {"prompt": "do it"}

    def _summary(self):
        path = (self.results_path() / "treatment" / "summary.json")
        return json.loads(path.read_text(encoding="utf-8"))

    def results_path(self):
        return self.args.results_dir / "guidance" / "s" / "T"

    def _run(self, *, guard_ok=True, agent=None):
        info = {"bytes": 1, "verdict": "installed", "installed": True,
                "returncode": 0, "dest": "x"}
        guard = {"ok": guard_ok, "expected": True, "observed": guard_ok,
                 "detail": "d"}
        with mock.patch.object(run_eval.guidance, "assemble", return_value="p"), \
             mock.patch.object(run_eval.guidance, "deliver", return_value=info), \
             mock.patch.object(run_eval.guidance, "agent_env", return_value={}), \
             mock.patch.object(run_eval.guidance, "run_guard", return_value=guard), \
             mock.patch.object(run_eval, "_guard_error",
                               return_value={"type": "guard", "detail": "d"}), \
             mock.patch.object(run_eval, "run_agent", return_value=agent or {
                 "transcript": "t", "usage": {}, "cost_usd": 0.0,
                 "num_turns": 1, "duration_ms": 1,
                 "raw": {"modelUsage": {"fake-model-a": {}}}}):
            return run_eval._run_guidance_arm(self.arm, self.fixture,
                                              self.tmp / "no-seed", self.ctx,
                                              self.args, "T")

    def test_a_scored_guidance_arm_records_both(self):
        result = self._run()
        summary = self._summary()
        self.assertEqual(summary["harness"],
                         {"name": "claude-code", "version": "9.9.9 (Claude Code)",
                          "permission_mode": "auto", "effort": None})
        self.assertEqual(summary["models_used"], ["fake-model-a"])
        self.assertEqual(result["models_used"], ["fake-model-a"])

    def test_a_guard_failure_records_the_harness_and_no_models(self):
        self._run(guard_ok=False)
        summary = self._summary()
        self.assertEqual(summary["harness"]["version"], "9.9.9 (Claude Code)")
        self.assertEqual(summary["models_used"], [])

    def test_a_judge_that_raises_after_a_cli_call_still_records_its_model(self):
        # F8 (#203 round 1), the guidance path's call site.
        from scorers import judge

        def score(*_args, **_kwargs):
            for sink in judge._MODEL_SINKS:
                sink.append({"modelUsage": {"fake-judge": {}}})
            raise ValueError("unparseable judge answer")
        self.args.no_judge = False
        self.fixture = {"prompt": "do it", "judge_rubric": "r"}
        with mock.patch.object(run_eval.judge, "score", score), \
             mock.patch.object(run_eval, "_git", return_value=types.SimpleNamespace(
                 stdout="", returncode=0)):
            self._run()
        summary = self._summary()
        self.assertIn("error", summary["judge"])
        self.assertEqual(summary["judge_models_used"], ["fake-judge"])

    def test_the_guidance_report_names_the_harness(self):
        report = run_eval._render_guidance_report(
            "s", "p", "T", "user", {"treatment": 1},
            [{"arm": "treatment", "mode": "section", "error": None,
              "models_used": ["fake-model-a"]}],
            harness_version="9.9.9 (Claude Code)")
        line = next(ln for ln in report.splitlines() if ln.startswith("- Harness:"))
        self.assertIn("9.9.9 (Claude Code)", line)
        self.assertIn("treatment=fake-model-a", line)


class TestJudgeSeamRecordsModels(unittest.TestCase):

    def test_the_collector_sees_every_judge_cli_call(self):
        from scorers import judge
        with mock.patch.dict(os.environ, {"CLAUDE_BIN": str(FAKE_CLAUDE),
                                          "FAKE_CLAUDE_MODE": "judge"}):
            with judge.collecting_models() as seen:
                judge._run_judge_cli("p", model="fake-judge", timeout=60)
            judge._run_judge_cli("p", model="not-collected", timeout=60)
        self.assertEqual(run_eval.models_used(*seen), ["fake-judge"])


# ---------------------------------------------------------------------------
# 3b. propagation's run record


class TestPropagationRecordsVersions(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _facts(self, init_extra):
        home = self.tmp / "clean-room" / "home"
        return init_probe.ProbeFacts(
            init={"skills": ["builtin"], **init_extra}, home=home,
            cwd=self.tmp / "clean-room" / "ws")

    def _record(self, init_extra):
        ctx = types.SimpleNamespace(root=self.tmp, timeout=60,
                                    lock={"skills": {"b/s": {}}})
        facts = self._facts(init_extra)
        out = self.tmp / "record.json"
        with mock.patch.object(run_propagation, "build_context", return_value=ctx), \
             mock.patch.object(run_propagation, "resolve_registry",
                               return_value=self.tmp), \
             mock.patch.object(arms, "_probe", return_value=facts), \
             contextlib.redirect_stdout(io.StringIO()):
            run_propagation.main([str(REPO_ROOT / "evals" / "propagation"),
                                  "--arm", "clean-room",
                                  "--json", str(out)])
        return json.loads(out.read_text(encoding="utf-8"))["arms"][0]

    def test_each_arm_carries_the_cli_version_and_model(self):
        arm = self._record({"claude_code_version": "2.9.9",
                            "model": "fake-model-a"})
        self.assertEqual(arm["harness_version"], "2.9.9")
        self.assertEqual(arm["model"], "fake-model-a")

    def test_absent_or_junk_fields_are_null(self):
        arm = self._record({"claude_code_version": ["x"], "model": "m" * 200})
        self.assertIsNone(arm["harness_version"])
        self.assertIsNone(arm["model"])

    def test_an_arm_that_faults_before_probing_carries_nulls(self):
        result = arms.ArmResult("x", arms.INCONCLUSIVE, error="boom").to_dict()
        self.assertIsNone(result["harness_version"])
        self.assertIsNone(result["model"])


if __name__ == "__main__":
    unittest.main()
