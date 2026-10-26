"""Tests for scripts/agent_check_lane.py and the runner features it relies on.

Every repo is a temp directory with a temp HOME and no global or system git
config. The candidate runners are temp copies of the template with one arm
inserted into check_lessons, the way the lesson-check lane adds one.

Run: python3 -m unittest discover -s test -p 'test_agent_check_lane.py'
(test/run-tests.sh's agent_checks group runs it that way).
"""

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LANE = REPO_ROOT / "scripts" / "agent_check_lane.py"
RUNNER = REPO_ROOT / "templates" / "agent-checks" / "agent-checks"
CASES = REPO_ROOT / "test" / "fixtures" / "agent-checks-cases.jsonl"
EMPTY_LESSONS = "check_lessons() {\n  :\n}\n"

# A path-based lesson: refuse any *.notes-secret file.
PATH_ARM = """check_lessons() {
  case "$1" in
    *.notes-secret)
      problem lesson-example-path "$1: notes files hold private notes and must not be committed. Move it outside the repo." ;; # lesson: example-path
  esac
}
"""

# A content-based lesson: refuse a .cfg file whose content holds a marker.
CONTENT_ARM = """check_lessons() {
  case "$1" in
    *.cfg)
      if file_content "$1" | grep -q 'DO-NOT-SHIP'; then
        problem lesson-example-content "$1: holds a DO-NOT-SHIP marker, left in by a draft. Remove the marker before committing."  # lesson: example-content
      fi ;;
  esac
}
"""


def yq_usable():
    if shutil.which("yq") is None:
        return False
    probe = subprocess.run(["yq", "-o=json", "-I0", "."], input="a: 1\n",
                           capture_output=True, text=True)
    return probe.returncode == 0


class LaneTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="agent-check-lane-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        home = self.tmp / "home"
        home.mkdir()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update(HOME=str(home), GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1",
                        AGENT_CHECKS_LOG="off")

    def git(self, repo, *args):
        return subprocess.run(["git", *args], cwd=str(repo), env=self.env,
                              capture_output=True, text=True, check=True).stdout

    def new_repo(self, name, files=None):
        repo = self.tmp / name
        repo.mkdir(parents=True)
        self.git(repo, "init", "-q", "-b", "main")
        self.git(repo, "config", "user.name", "Example")
        self.git(repo, "config", "user.email", "agent@example.com")
        self.git(repo, "config", "commit.gpgsign", "false")
        self.git(repo, "commit", "-q", "--allow-empty", "-m", "base")
        if files:
            self.commit(repo, files)
        return repo

    def write(self, repo, files):
        for rel, content in files.items():
            target = repo / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

    def commit(self, repo, files, message="files"):
        self.write(repo, files)
        self.git(repo, "add", "--", *files)
        self.git(repo, "commit", "-q", "-m", message)

    def candidate(self, arm, name="candidate"):
        text = RUNNER.read_text(encoding="utf-8")
        self.assertEqual(text.count(EMPTY_LESSONS), 1, "the template's check_lessons is no longer empty")
        path = self.tmp / name
        path.write_text(text.replace(EMPTY_LESSONS, arm), encoding="utf-8")
        return path

    def runner(self, runner, repo, *args):
        return subprocess.run(["bash", str(runner), *args], cwd=str(repo), env=self.env,
                              capture_output=True, text=True)

    def lane(self, *args, cwd=None, env=None):
        return subprocess.run([sys.executable, str(LANE), *args], cwd=str(cwd or REPO_ROOT),
                              env=env or self.env, capture_output=True, text=True)

    def cases_file(self, *lines, include_default=True):
        path = self.tmp / "cases.jsonl"
        base = CASES.read_text(encoding="utf-8") if include_default else ""
        path.write_text(base + "".join(line + "\n" for line in lines), encoding="utf-8")
        return path


class CasesTests(LaneTestCase):
    def test_cases_pass_on_current_runner(self):
        out = self.lane("cases", "--runner", str(RUNNER))
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn(" passed, 0 failed", out.stdout)
        self.assertNotIn("FAIL", out.stdout)

    def test_wrong_expectation_is_reported(self):
        cases = self.cases_file(
            '{"id": "env-wrongly-passes", "event": "pre-commit", "files": {".env": "A=1\\n"}, "expect": "pass"}',
            include_default=False)
        out = self.lane("cases", "--runner", str(RUNNER), "--cases", str(cases))
        self.assertEqual(out.returncode, 1, out.stdout)
        self.assertIn("FAIL env-wrongly-passes (expected pass, got exit 1)", out.stdout)

    def test_refusal_without_the_message_is_reported(self):
        cases = self.cases_file(
            '{"id": "env-other-message", "event": "tree", "files": {".env": "A=1\\n"}, "expect": "refuse", "message": "no such text"}',
            include_default=False)
        out = self.lane("cases", "--runner", str(RUNNER), "--cases", str(cases))
        self.assertEqual(out.returncode, 1, out.stdout)
        self.assertIn("FAIL env-other-message (refused, but stderr lacks the expected message)", out.stdout)


class RedGreenTests(LaneTestCase):
    def test_good_lesson_passes(self):
        cases = self.cases_file(
            '{"id": "notes-refused", "lesson": "example-path", "event": "pre-commit", "files": {"a/b.notes-secret": "x\\n"}, "expect": "refuse", "message": "a/b.notes-secret: notes files hold private notes"}',
            '{"id": "notes-near-miss", "lesson": "example-path", "event": "pre-commit", "files": {"a/b.notes": "x\\n"}, "expect": "pass"}')
        out = self.lane("red-green", "--base", str(RUNNER), "--candidate", str(self.candidate(PATH_ARM)),
                        "--lesson", "example-path", "--cases", str(cases))
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertIn("PASS red notes-refused", out.stdout)
        self.assertIn("PASS green notes-near-miss", out.stdout)

    def test_case_that_already_refuses_under_base_fails(self):
        cases = self.cases_file(
            '{"id": "env-tagged", "lesson": "example-path", "event": "pre-commit", "files": {".env": "A=1\\n"}, "expect": "refuse", "message": ".env: looks like a secret"}')
        out = self.lane("red-green", "--base", str(RUNNER), "--candidate", str(self.candidate(PATH_ARM)),
                        "--lesson", "example-path", "--cases", str(cases))
        self.assertEqual(out.returncode, 1, out.stdout)
        self.assertIn("FAIL red env-tagged (already refuses under the base runner", out.stdout)

    def test_no_tagged_case_fails(self):
        out = self.lane("red-green", "--base", str(RUNNER), "--candidate", str(self.candidate(PATH_ARM)),
                        "--lesson", "example-path", "--cases", str(CASES))
        self.assertEqual(out.returncode, 1, out.stdout)
        self.assertIn("FAIL no case is tagged with lesson example-path", out.stdout)

    def test_only_pass_cases_tagged_fails(self):
        cases = self.cases_file(
            '{"id": "notes-near-miss", "lesson": "example-path", "event": "pre-commit", "files": {"a/b.notes": "x\\n"}, "expect": "pass"}')
        out = self.lane("red-green", "--base", str(RUNNER), "--candidate", str(self.candidate(PATH_ARM)),
                        "--lesson", "example-path", "--cases", str(cases))
        self.assertEqual(out.returncode, 1, out.stdout)
        self.assertIn("FAIL no refuse case is tagged", out.stdout)

    def test_candidate_breaking_an_existing_case_fails(self):
        broken = PATH_ARM.replace("*.notes-secret)", "*)")
        cases = self.cases_file(
            '{"id": "notes-refused", "lesson": "example-path", "event": "pre-commit", "files": {"a/b.notes-secret": "x\\n"}, "expect": "refuse", "message": "notes files hold private notes"}')
        out = self.lane("red-green", "--base", str(RUNNER), "--candidate", str(self.candidate(broken)),
                        "--lesson", "example-path", "--cases", str(cases))
        self.assertEqual(out.returncode, 1, out.stdout)
        self.assertIn("FAIL green readme-passes", out.stdout)


class RunnerTreeTests(LaneTestCase):
    def test_tree_checks_every_path_of_a_revision(self):
        repo = self.new_repo("r", {"README.md": "# Example\n", "certs/site.pem": "not a real cert\n"})
        out = self.runner(RUNNER, repo, "tree")
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn("agent-checks: certs/site.pem: looks like a secret", out.stderr)
        self.git(repo, "rm", "-q", "certs/site.pem")
        self.git(repo, "commit", "-q", "-m", "remove")
        self.assertEqual(self.runner(RUNNER, repo, "tree", "HEAD").returncode, 0)
        out = self.runner(RUNNER, repo, "tree", "HEAD~1")
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn("certs/site.pem", out.stderr)

    def test_tree_refuses_an_unknown_revision(self):
        repo = self.new_repo("r", {"README.md": "# Example\n"})
        out = self.runner(RUNNER, repo, "tree", "no-such-branch")
        self.assertEqual(out.returncode, 1)
        self.assertIn("tree: 'no-such-branch' is not a commit or tree", out.stderr)


class ContentArmTests(LaneTestCase):
    def setUp(self):
        super().setUp()
        self.cand = self.candidate(CONTENT_ARM)

    def test_pre_commit_reads_the_index_not_the_worktree(self):
        repo = self.new_repo("r")
        self.write(repo, {"app.cfg": "mode=DO-NOT-SHIP\n"})
        self.git(repo, "add", "app.cfg")
        self.write(repo, {"app.cfg": "mode=release\n"})
        out = self.runner(self.cand, repo, "pre-commit")
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn("agent-checks: app.cfg: holds a DO-NOT-SHIP marker", out.stderr)
        self.assertNotIn("mode=", out.stderr)

        self.git(repo, "add", "app.cfg")
        self.write(repo, {"app.cfg": "mode=DO-NOT-SHIP\n"})
        out = self.runner(self.cand, repo, "pre-commit")
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_ci_reads_the_head(self):
        repo = self.new_repo("r")
        base = self.git(repo, "rev-parse", "HEAD").strip()
        self.commit(repo, {"app.cfg": "mode=DO-NOT-SHIP\n", "other.cfg": "mode=release\n"})
        out = self.runner(self.cand, repo, "ci", base, "HEAD")
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn("app.cfg: holds a DO-NOT-SHIP marker", out.stderr)
        self.assertNotIn("other.cfg", out.stderr)

    def test_tree_reads_the_named_revision(self):
        repo = self.new_repo("r", {"app.cfg": "mode=DO-NOT-SHIP\n"})
        self.commit(repo, {"app.cfg": "mode=release\n"})
        self.assertEqual(self.runner(self.cand, repo, "tree", "HEAD").returncode, 0)
        out = self.runner(self.cand, repo, "tree", "HEAD~1")
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn("app.cfg: holds a DO-NOT-SHIP marker", out.stderr)


class LocalChecksTests(LaneTestCase):
    """.agent-checks/local.sh: a repo's own checks, run after the fleet's."""

    def test_local_check_exiting_0_cannot_suppress_a_fleet_refusal(self):
        repo = self.new_repo("r")
        self.write(repo, {".agent-checks/local.sh": "failed=0\nexit 0\n", ".env": "A=1\n"})
        self.git(repo, "add", ".agent-checks/local.sh", ".env")
        out = self.runner(RUNNER, repo, "pre-commit")
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn("agent-checks: .env: looks like a secret", out.stderr)

    def test_local_check_reads_paths_rev_and_event(self):
        script = ('rev=$AGENT_CHECKS_REV\n'
                  'while IFS= read -r -d "" p; do\n'
                  '  if git show "${rev}:$p" | grep -qx DRAFT; then echo "local: $1: $p is a draft" >&2; exit 3; fi\n'
                  'done\n')
        repo = self.new_repo("r", {".agent-checks/local.sh": script, "doc.md": "DRAFT\n"})
        out = self.runner(RUNNER, repo, "tree", "HEAD")
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn("local: tree: doc.md is a draft", out.stderr)
        self.assertIn("agent-checks: .agent-checks/local.sh (this repo's own checks) refused, exit 3", out.stderr)
        # pre-commit: an empty AGENT_CHECKS_REV reads the index.
        self.write(repo, {"doc.md": "final\n"})
        self.git(repo, "add", "doc.md")
        self.write(repo, {"doc.md": "DRAFT\n"})
        out = self.runner(RUNNER, repo, "pre-commit")
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_symlinked_local_check_is_not_run(self):
        repo = self.new_repo("r")
        (self.tmp / "outside.sh").write_text("exit 1\n", encoding="utf-8")
        (repo / ".agent-checks").mkdir()
        (repo / ".agent-checks" / "local.sh").symlink_to(self.tmp / "outside.sh")
        self.write(repo, {"README.md": "# Example\n"})
        self.git(repo, "add", "README.md")
        self.assertEqual(self.runner(RUNNER, repo, "pre-commit").returncode, 0)


@unittest.skipUnless(yq_usable(), "fleet-scan reads repos.yml with mikefarah yq, which is not on PATH "
                                  "(CI's suite job installs it in its 'Install yq' step)")
class FleetScanTests(LaneTestCase):
    def setUp(self):
        super().setUp()
        self.root = self.tmp / "fleet"
        self.repos_yml = self.tmp / "repos.yml"
        self.repos_yml.write_text("cron_coverage:\n  fleet:\n    - alpha\n    - beta\n", encoding="utf-8")
        # alpha already holds a file the base runner refuses: not new.
        self.alpha = self.new_repo("fleet/alpha", {"README.md": "# Alpha\n", ".env": "A=1\n"})
        self.beta = self.new_repo("fleet/beta", {"README.md": "# Beta\n"})
        self.cand = self.candidate(PATH_ARM)

    def scan(self):
        return self.lane("fleet-scan", "--base", str(RUNNER), "--candidate", str(self.cand),
                         "--root", str(self.root), "--repos-yml", str(self.repos_yml))

    def test_no_new_refusal_exits_0(self):
        out = self.scan()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("alpha: 0 new refusal(s)", out.stdout)
        self.assertIn("beta: 0 new refusal(s)", out.stdout)

    def test_new_refusal_exits_1(self):
        self.commit(self.beta, {"docs/plan.notes-secret": "x\n"})
        out = self.scan()
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertIn("beta: 1 new refusal(s)", out.stdout)
        self.assertIn("  agent-checks: docs/plan.notes-secret: notes files", out.stdout)
        self.assertIn("alpha: 0 new refusal(s)", out.stdout)

    def test_missing_checkout_exits_2(self):
        self.repos_yml.write_text("cron_coverage:\n  fleet:\n    - alpha\n    - beta\n    - gamma\n",
                                  encoding="utf-8")
        self.commit(self.beta, {"docs/plan.notes-secret": "x\n"})
        out = self.scan()
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("gamma: NOT REACHED (no checkout)", out.stdout)
        self.assertIn("NOT REACHED: gamma", out.stdout)

    def test_plain_directory_is_not_reached(self):
        shutil.rmtree(self.beta / ".git")
        out = self.scan()
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("beta: NOT REACHED (not a git checkout)", out.stdout)


class ScopeTests(LaneTestCase):
    ALLOWED = {
        "templates/agent-checks/agent-checks": "runner v1\n",
        ".githooks/agent-checks": "runner v1\n",
        "test/fixtures/agent-checks-cases.jsonl": "{}\n",
        "README.md": "# Example\n",
    }

    def setUp(self):
        super().setUp()
        self.repo = self.new_repo("r", self.ALLOWED)
        self.base = self.git(self.repo, "rev-parse", "HEAD").strip()

    def scope(self):
        return self.lane("scope", "--base", self.base, "--head", "HEAD", cwd=self.repo)

    def test_allowed_changes_pass(self):
        self.commit(self.repo, {"templates/agent-checks/agent-checks": "runner v2\n",
                                ".githooks/agent-checks": "runner v2\n",
                                "test/fixtures/agent-checks-cases.jsonl": "{}\n{}\n",
                                "docs/agent-checks-lessons.md": "# Ledger\n"})
        out = self.scope()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_another_path_fails(self):
        self.commit(self.repo, {"templates/agent-checks/agent-checks": "runner v2\n",
                                ".githooks/agent-checks": "runner v2\n",
                                "scripts/sync.sh": "echo\n"})
        out = self.scope()
        self.assertEqual(out.returncode, 1, out.stdout)
        self.assertIn("scope: A scripts/sync.sh: outside the lesson-check lane's files", out.stdout)

    def test_deletion_fails(self):
        self.git(self.repo, "rm", "-q", "test/fixtures/agent-checks-cases.jsonl")
        self.git(self.repo, "commit", "-q", "-m", "remove")
        out = self.scope()
        self.assertEqual(out.returncode, 1, out.stdout)
        self.assertIn("scope: D test/fixtures/agent-checks-cases.jsonl: only adds and modifies", out.stdout)

    def test_differing_runner_copies_fail(self):
        self.commit(self.repo, {"templates/agent-checks/agent-checks": "runner v2\n"})
        out = self.scope()
        self.assertEqual(out.returncode, 1, out.stdout)
        self.assertIn("is not byte-identical", out.stdout)


LOG_KEYS = ["v", "ts", "event", "repo", "checks", "failed"]
KEBAB = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


class RunLogTests(LaneTestCase):
    """The run log: one JSON line per pre-commit or pre-push run."""

    def setUp(self):
        super().setUp()
        self.log = self.tmp / "state" / "agent-checks" / "runs.jsonl"
        self.repo = self.new_repo("r")
        self.git(self.repo, "remote", "add", "origin", "https://github.com/example-owner/widgets.git")

    def run_logged(self, *args, log=None, stdin=None):
        env = dict(self.env, AGENT_CHECKS_LOG=str(log or self.log))
        return subprocess.run(["bash", str(RUNNER), *args], cwd=str(self.repo), env=env,
                              input=stdin, capture_output=True, text=True)

    def entries(self):
        if not self.log.exists():
            return []
        out = []
        for line in self.log.read_text(encoding="utf-8").splitlines():
            entry = json.loads(line, object_pairs_hook=lambda pairs: pairs)
            self.assertEqual([k for k, _ in entry], LOG_KEYS, line)
            out.append(dict(entry))
        return out

    def stage(self, files):
        self.write(self.repo, files)
        self.git(self.repo, "add", "--", *files)

    def test_passing_pre_commit_writes_one_line(self):
        self.stage({"README.md": "# Example\n"})
        out = self.run_logged("pre-commit")
        self.assertEqual((out.returncode, out.stdout, out.stderr), (0, "", ""))
        [entry] = self.entries()
        self.assertEqual(entry["v"], 1)
        self.assertRegex(entry["ts"], r"\A\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\Z")
        self.assertEqual(entry["event"], "pre-commit")
        self.assertEqual(entry["repo"], "example-owner/widgets")
        self.assertEqual(entry["checks"], ["rules-dir", "secret-file"])
        self.assertEqual(entry["failed"], [])

    def test_refused_pre_commit_lists_the_check_and_keeps_the_message(self):
        self.stage({".env": "A=1\n"})
        silent = self.run_logged("pre-commit", log="off")
        out = self.run_logged("pre-commit")
        self.assertEqual(out.returncode, 1)
        self.assertEqual((out.stdout, out.stderr), (silent.stdout, silent.stderr))
        self.assertIn("agent-checks: .env: looks like a secret or credential file", out.stderr)
        [entry] = self.entries()
        self.assertEqual(entry["failed"], ["secret-file"])

    def test_pre_push_to_default_lists_push_to_default(self):
        self.commit(self.repo, {"README.md": "# Example\n"})
        head = self.git(self.repo, "rev-parse", "HEAD").strip()
        out = self.run_logged("pre-push", "origin", "https://github.com/example-owner/widgets.git",
                              stdin=f"refs/heads/main {head} refs/heads/main {'0' * 40}\n")
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn("refusing to push to main", out.stderr)
        [entry] = self.entries()
        self.assertEqual(entry["event"], "pre-push")
        self.assertEqual(entry["checks"], ["staged-index", "push-to-default", "rules-dir", "secret-file"])
        self.assertEqual(entry["failed"], ["push-to-default"])

    def test_repo_local_check_is_logged(self):
        self.stage({".agent-checks/local.sh": "exit 3\n"})
        out = self.run_logged("pre-commit")
        self.assertEqual(out.returncode, 1, out.stderr)
        [entry] = self.entries()
        self.assertEqual(entry["checks"], ["rules-dir", "secret-file", "repo-local"])
        self.assertEqual(entry["failed"], ["repo-local"])

    def test_ci_tree_and_unknown_events_write_nothing(self):
        base = self.git(self.repo, "rev-parse", "HEAD").strip()
        self.commit(self.repo, {".env": "A=1\n"})
        self.assertEqual(self.run_logged("ci", base, "HEAD").returncode, 1)
        self.assertEqual(self.run_logged("tree", "HEAD").returncode, 1)
        self.assertEqual(self.run_logged("post-merge").returncode, 0)
        self.assertFalse(self.log.exists())

    def test_off_writes_nothing(self):
        self.stage({"README.md": "# Example\n"})
        env = dict(self.env, AGENT_CHECKS_LOG="off", XDG_STATE_HOME=str(self.tmp / "state"))
        out = subprocess.run(["bash", str(RUNNER), "pre-commit"], cwd=str(self.repo), env=env,
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertFalse((self.tmp / "state").exists())

    def test_default_path_is_under_xdg_state_home(self):
        self.stage({"README.md": "# Example\n"})
        env = {k: v for k, v in self.env.items() if k != "AGENT_CHECKS_LOG"}
        env["XDG_STATE_HOME"] = str(self.tmp / "state")
        out = subprocess.run(["bash", str(RUNNER), "pre-commit"], cwd=str(self.repo), env=env,
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(len(self.entries()), 1)

    def test_unwritable_log_changes_nothing(self):
        blocker = self.tmp / "blocker"
        blocker.write_text("a file, so no directory can be made under it\n", encoding="utf-8")
        for files, rc in (({"README.md": "# Example\n"}, 0), ({".env": "A=1\n"}, 1)):
            self.stage(files)
            silent = self.run_logged("pre-commit", log="off")
            out = self.run_logged("pre-commit", log=blocker / "agent-checks" / "runs.jsonl")
            self.assertEqual((out.returncode, out.stdout, out.stderr),
                             (silent.returncode, silent.stdout, silent.stderr))
            self.assertEqual(out.returncode, rc)

    def test_repo_is_parsed_only_from_github_remotes(self):
        self.stage({"README.md": "# Example\n"})
        urls = {
            "git@github.com:example-owner/widgets.git": "example-owner/widgets",
            "ssh://git@github.com/example-owner/widgets": "example-owner/widgets",
            "https://github.com/example-owner/my.widgets_2": "example-owner/my.widgets_2",
            "https://gitlab.example.com/example-owner/widgets.git": None,
            "/srv/git/widgets.git": None,
            "https://user:pass@github.com/example-owner/widgets.git": None,
            "https://github.com/example-owner/widgets/extra": None,
            'https://github.com/example owner/wid"gets.git': None,
            "git@github.com:example-owner/wid'gets\\\".git": None,
        }
        for url, want in urls.items():
            self.git(self.repo, "remote", "set-url", "origin", url)
            self.assertEqual(self.run_logged("pre-commit").returncode, 0)
            self.assertEqual(self.entries()[-1]["repo"], want, url)
        self.git(self.repo, "remote", "remove", "origin")
        self.run_logged("pre-commit")
        self.assertIsNone(self.entries()[-1]["repo"])
        self.assertEqual(len(self.entries()), len(urls) + 1)

    def test_lane_cases_write_nothing(self):
        env = {k: v for k, v in self.env.items() if k != "AGENT_CHECKS_LOG"}
        env["XDG_STATE_HOME"] = str(self.tmp / "state")
        out = self.lane("cases", "--runner", str(RUNNER), env=env)
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertFalse((self.tmp / "state").exists())


def canonical_runner(runner):
    """The runner as bash itself parsed it: the whole file is wrapped in a
    function that is defined, never called, and printed with declare -f, so
    comments are gone and each command sits on its own line."""
    script = 'eval "__agent_checks_all() {\n$(cat "$1")\n}" && declare -f __agent_checks_all'
    out = subprocess.run(["bash", "-c", script, "bash", str(runner)],
                         capture_output=True, text=True, check=True)
    return out.stdout


def runner_lesson_parts(runner):
    """check_lessons as bash printed it, and the ids lesson_checks names."""
    script = ('trap \'declare -f check_lessons; echo ===; printf "%s\\n" $lesson_checks\' EXIT; '
              'source "$1" not-an-event')
    out = subprocess.run(["bash", "-c", script, "bash", str(runner)],
                         capture_output=True, text=True, check=True).stdout
    body, _, ids = out.partition("===\n")
    return body, ids.split()


# Tokens after which the next word is a command name.
COMMAND_START = {";", ";;", "&&", "||", "|", "&", "(", ")", "{", "!", "then", "do", "else", "elif",
                 "if", "while", "until"}


def problem_calls(text):
    """(id token, raw line) for every `problem` command in canonical bash."""
    calls = []
    for raw in text.splitlines():
        if not re.search(r"\bproblem\b", raw):
            continue
        lex = shlex.shlex(raw, posix=True, punctuation_chars=";&|(){}!")
        lex.commenters = ""
        lex.whitespace_split = True
        tokens = list(lex)
        for i, tok in enumerate(tokens):
            if tok != "problem":
                continue
            if i > 0 and tokens[i - 1] == "function":
                continue  # the definition
            if i > 0 and tokens[i - 1] not in COMMAND_START:
                raise AssertionError(f"`problem` is not at a command position: {raw.strip()}")
            calls.append((tokens[i + 1] if i + 1 < len(tokens) else None, raw))
    return calls


class CheckIdTests(unittest.TestCase):
    """Every refusal names a stable check id: the run log counts by it."""

    def test_every_problem_call_passes_a_literal_kebab_case_id(self):
        calls = problem_calls(canonical_runner(RUNNER))
        self.assertGreaterEqual(len(calls), 7)
        for ident, raw in calls:
            self.assertIsNotNone(ident, raw)
            self.assertRegex(ident, KEBAB, raw)
            self.assertRegex(raw, r"\bproblem " + re.escape(ident) + r"(\s|;|$)",
                             f"the id must be an unquoted literal: {raw.strip()}")

    def test_core_check_ids(self):
        ids = {ident for ident, _ in problem_calls(canonical_runner(RUNNER))}
        self.assertLessEqual({"rules-dir", "secret-file", "staged-index", "push-to-default",
                              "repo-local"}, ids)

    def test_lesson_arms_and_lesson_checks_agree(self):
        for arm in (None, PATH_ARM):
            runner = RUNNER
            if arm:
                tmp = Path(tempfile.mkdtemp(prefix="agent-check-ids-"))
                self.addCleanup(shutil.rmtree, tmp, True)
                runner = tmp / "candidate"
                text = RUNNER.read_text(encoding="utf-8").replace(EMPTY_LESSONS, arm)
                runner.write_text(text.replace('lesson_checks=""', 'lesson_checks="lesson-example-path"'),
                                  encoding="utf-8")
            body, listed = runner_lesson_parts(runner)
            arm_ids = [ident for ident, _ in problem_calls(body)]
            self.assertTrue(all(i.startswith("lesson-") for i in arm_ids + listed), arm_ids + listed)
            self.assertEqual(sorted(set(arm_ids)), sorted(listed))
            others = {i for i, _ in problem_calls(canonical_runner(runner))} - set(arm_ids)
            self.assertFalse([i for i in others if i.startswith("lesson-")])


if __name__ == "__main__":
    unittest.main()
