"""Tests for scripts/agent_check_lane.py and the runner features it relies on.

Every repo is a temp directory with a temp HOME and no global or system git
config. The candidate runners are temp copies of the template with one arm
inserted into check_lessons, the way the lesson-check lane adds one.

Run: python3 -m unittest discover -s test -p 'test_agent_check_lane.py'
(test/run-tests.sh's agent_checks group runs it that way).
"""

import os
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
      problem "$1: notes files hold private notes and must not be committed. Move it outside the repo." ;; # lesson: example-path
  esac
}
"""

# A content-based lesson: refuse a .cfg file whose content holds a marker.
CONTENT_ARM = """check_lessons() {
  case "$1" in
    *.cfg)
      if file_content "$1" | grep -q 'DO-NOT-SHIP'; then
        problem "$1: holds a DO-NOT-SHIP marker, left in by a draft. Remove the marker before committing."  # lesson: example-content
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
        self.env.update(HOME=str(home), GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")

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


if __name__ == "__main__":
    unittest.main()
