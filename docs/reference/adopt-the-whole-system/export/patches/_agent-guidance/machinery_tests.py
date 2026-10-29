"""DECISIONS X9, second half: the tests follow the blank content.

blank_content.py takes the fleet checks out of the agent-checks runner and
the rules out of base.md. The tests below asserted on that content. Each one
either moves to a test-only fixture (a check that lives only in the test, so
the runner's machinery is still exercised) or, where it only locked content
the kit no longer ships, is removed. Every change is listed in
export/DECISIONS.md (X9, "Tests") with its reason.

Runs on the substituted tree, after blank_content.py.
"""

from __future__ import annotations

import json
from pathlib import Path


def _sub(root: Path, rel: str, pairs) -> None:
    path = root / rel
    text = path.read_text(encoding="utf-8")
    for old, new in pairs:
        n = text.count(old)
        if n != 1:
            raise SystemExit(f"machinery_tests: expected 1 occurrence in {rel}, found {n}: {old[:70]!r}")
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")


# ── test/fixtures/agent-checks-cases.jsonl ──────────────────────────────────
# The lane's case table. Kept: the cases that exercise the runner itself
# (events, ranges, the repo-local check). Removed: the cases for the fleet
# checks the kit does not ship. Two range cases keep their shape with the
# repo-local draft check standing in for the removed secret-file check.
KEEP_CASES = ["readme-passes", "ci-ordinary-passes", "tree-ordinary-passes",
              "local-check-refuses", "local-check-passes"]
DROP_CASES = ["rules-dir-refused", "claude-dir-other-file-passes", "env-refused", "env-local-refused",
              "env-example-passes", "pem-refused", "ssh-key-refused", "skill-shell-inline-refused",
              "skill-shell-fenced-refused", "skill-shell-command-file-refused",
              "skill-shell-plugin-command-refused", "skill-shell-code-span-passes",
              "skill-shell-in-readme-passes"]
CONVERT_CASES = ["ci-earlier-commit-caught", "tree-refuses-committed-key"]


def _cases(root: Path) -> None:
    path = root / "test" / "fixtures" / "agent-checks-cases.jsonl"
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    cases = {json.loads(l)["id"]: (l, json.loads(l)) for l in lines}
    want = set(KEEP_CASES) | set(DROP_CASES) | set(CONVERT_CASES)
    if set(cases) != want:
        raise SystemExit(f"machinery_tests: case ids changed: {sorted(set(cases) ^ want)}")
    local = cases["local-check-refuses"][1]["files"][".agent-checks/local.sh"]
    out = []
    for line in lines:
        cid = json.loads(line)["id"]
        if cid in KEEP_CASES:
            out.append(line)
        elif cid == "ci-earlier-commit-caught":
            out.append(json.dumps({"id": "ci-earlier-commit-caught", "event": "ci",
                                   "files": {".agent-checks/local.sh": local, "notes/plan.draft": "x\n",
                                             "README.md": "# Example\n"},
                                   "delete": ["notes/plan.draft"], "expect": "refuse",
                                   "message": "notes/plan.draft: drafts stay out of this repo"}))
        elif cid == "tree-refuses-committed-key":
            out.append(json.dumps({"id": "tree-refuses-committed-draft", "event": "tree",
                                   "files": {".agent-checks/local.sh": local, "deploy/release.draft": "x\n",
                                             "README.md": "# Example\n"},
                                   "expect": "refuse", "message": "deploy/release.draft: drafts stay out of this repo"}))
    path.write_text("".join(l + "\n" for l in out), encoding="utf-8")


# ── test/test_agent_check_lane.py ────────────────────────────────────────────
LANE_EDITS = [
    # A test-only runner with one check, for the machinery tests that need a
    # runner that refuses something.
    ('''def yq_usable():''',
     '''# The kit's runner ships no checks (DECISIONS X9). The tests of its
# machinery that need a check to refuse something use this test-only fixture:
# the template with PATH_ARM in its lesson slot and the id listed.
FIXTURE_CHECK = "lesson-example-path"

# A repo-local check (.agent-checks/local.sh) that refuses *.draft files.
DRAFT_LOCAL = ('status=0\\n'
               'while IFS= read -r -d "" path; do\\n'
               '  case "$path" in *.draft) echo "agent-checks (local): $path: drafts stay out of this repo." >&2; status=1 ;; esac\\n'
               'done\\n'
               'exit $status\\n')


def yq_usable():'''),
    ('''    def runner(self, runner, repo, *args):''',
     '''    def fixture_runner(self):
        path = self.candidate(PATH_ARM, name="fixture-runner")
        text = path.read_text(encoding="utf-8")
        self.assertEqual(text.count('lesson_checks=""'), 1)
        path.write_text(text.replace('lesson_checks=""', f'lesson_checks="{FIXTURE_CHECK}"'), encoding="utf-8")
        return path

    def runner(self, runner, repo, *args):'''),
    # CasesTests: the reporting is machinery; the fixture runner refuses.
    ('''        cases = self.cases_file(
            '{"id": "env-wrongly-passes", "event": "pre-commit", "files": {".env": "A=1\\\\n"}, "expect": "pass"}',
            include_default=False)
        out = self.lane("cases", "--runner", str(RUNNER), "--cases", str(cases))
        self.assertEqual(out.returncode, 1, out.stdout)
        self.assertIn("FAIL env-wrongly-passes (expected pass, got exit 1)", out.stdout)''',
     '''        cases = self.cases_file(
            '{"id": "notes-wrongly-passes", "event": "pre-commit", "files": {"a.notes-secret": "x\\\\n"}, "expect": "pass"}',
            include_default=False)
        out = self.lane("cases", "--runner", str(self.fixture_runner()), "--cases", str(cases))
        self.assertEqual(out.returncode, 1, out.stdout)
        self.assertIn("FAIL notes-wrongly-passes (expected pass, got exit 1)", out.stdout)'''),
    ('''        cases = self.cases_file(
            '{"id": "env-other-message", "event": "tree", "files": {".env": "A=1\\\\n"}, "expect": "refuse", "message": "no such text"}',
            include_default=False)
        out = self.lane("cases", "--runner", str(RUNNER), "--cases", str(cases))
        self.assertEqual(out.returncode, 1, out.stdout)
        self.assertIn("FAIL env-other-message (refused, but stderr lacks the expected message)", out.stdout)''',
     '''        cases = self.cases_file(
            '{"id": "notes-other-message", "event": "tree", "files": {"a.notes-secret": "x\\\\n"}, "expect": "refuse", "message": "no such text"}',
            include_default=False)
        out = self.lane("cases", "--runner", str(self.fixture_runner()), "--cases", str(cases))
        self.assertEqual(out.returncode, 1, out.stdout)
        self.assertIn("FAIL notes-other-message (refused, but stderr lacks the expected message)", out.stdout)'''),
    # RedGreenTests: the base runner's own refusal is now the repo-local check.
    ('''        cases = self.cases_file(
            '{"id": "env-tagged", "lesson": "example-path", "event": "pre-commit", "files": {".env": "A=1\\\\n"}, "expect": "refuse", "message": ".env: looks like a secret"}')
        out = self.lane("red-green", "--base", str(RUNNER), "--candidate", str(self.candidate(PATH_ARM)),
                        "--lesson", "example-path", "--cases", str(cases))
        self.assertEqual(out.returncode, 1, out.stdout)
        self.assertIn("FAIL red env-tagged (already refuses under the base runner", out.stdout)''',
     '''        case = {"id": "draft-tagged", "lesson": "example-path", "event": "pre-commit",
                "files": {".agent-checks/local.sh": DRAFT_LOCAL, "plan.draft": "x\\n"},
                "expect": "refuse", "message": "plan.draft: drafts stay out"}
        cases = self.cases_file(json.dumps(case))
        out = self.lane("red-green", "--base", str(RUNNER), "--candidate", str(self.candidate(PATH_ARM)),
                        "--lesson", "example-path", "--cases", str(cases))
        self.assertEqual(out.returncode, 1, out.stdout)
        self.assertIn("FAIL red draft-tagged (already refuses under the base runner", out.stdout)'''),
    # RunnerTreeTests: `tree` checks every path, with the fixture check.
    ('''        repo = self.new_repo("r", {"README.md": "# Example\\n", "certs/site.pem": "not a real cert\\n"})
        out = self.runner(RUNNER, repo, "tree")
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn("agent-checks: certs/site.pem: looks like a secret", out.stderr)
        self.git(repo, "rm", "-q", "certs/site.pem")
        self.git(repo, "commit", "-q", "-m", "remove")
        self.assertEqual(self.runner(RUNNER, repo, "tree", "HEAD").returncode, 0)
        out = self.runner(RUNNER, repo, "tree", "HEAD~1")
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn("certs/site.pem", out.stderr)''',
     '''        fixture = self.fixture_runner()
        repo = self.new_repo("r", {"README.md": "# Example\\n", "certs/site.notes-secret": "x\\n"})
        out = self.runner(fixture, repo, "tree")
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn("agent-checks: certs/site.notes-secret: notes files", out.stderr)
        self.git(repo, "rm", "-q", "certs/site.notes-secret")
        self.git(repo, "commit", "-q", "-m", "remove")
        self.assertEqual(self.runner(fixture, repo, "tree", "HEAD").returncode, 0)
        out = self.runner(fixture, repo, "tree", "HEAD~1")
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn("certs/site.notes-secret", out.stderr)'''),
    # LocalChecksTests: a local check cannot clear the fixture check's refusal.
    ('''        self.write(repo, {".agent-checks/local.sh": "failed=0\\nexit 0\\n", ".env": "A=1\\n"})
        self.git(repo, "add", ".agent-checks/local.sh", ".env")
        out = self.runner(RUNNER, repo, "pre-commit")
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn("agent-checks: .env: looks like a secret", out.stderr)''',
     '''        self.write(repo, {".agent-checks/local.sh": "failed=0\\nexit 0\\n", "a.notes-secret": "x\\n"})
        self.git(repo, "add", ".agent-checks/local.sh", "a.notes-secret")
        out = self.runner(self.fixture_runner(), repo, "pre-commit")
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn("agent-checks: a.notes-secret: notes files", out.stderr)'''),
    # FleetScanTests: alpha's existing refusal is its own repo-local check.
    ('''        # alpha already holds a file the base runner refuses: not new.
        self.alpha = self.new_repo("fleet/alpha", {"README.md": "# Alpha\\n", ".env": "A=1\\n"})''',
     '''        # alpha already holds a file the base runner refuses (its own
        # repo-local check refuses it): not new.
        self.alpha = self.new_repo("fleet/alpha", {"README.md": "# Alpha\\n", ".agent-checks/local.sh": DRAFT_LOCAL,
                                                   "plan.draft": "x\\n"})'''),
    # RunLogTests: the log records the fixture check's id.
    ('''    def run_logged(self, *args, log=None, stdin=None):
        env = dict(self.env, AGENT_CHECKS_LOG=str(log or self.log))
        return subprocess.run(["bash", str(RUNNER), *args], cwd=str(self.repo), env=env,''',
     '''    def run_logged(self, *args, log=None, stdin=None):
        env = dict(self.env, AGENT_CHECKS_LOG=str(log or self.log))
        if not hasattr(self, "fixture"):
            self.fixture = self.fixture_runner()
        return subprocess.run(["bash", str(self.fixture), *args], cwd=str(self.repo), env=env,'''),
    ('''        self.assertEqual(entry["checks"], ["rules-dir", "secret-file", "skill-shell"])
        self.assertEqual(entry["failed"], [])''',
     '''        self.assertEqual(entry["checks"], [FIXTURE_CHECK])
        self.assertEqual(entry["failed"], [])'''),
    ('''        self.stage({".env": "A=1\\n"})
        silent = self.run_logged("pre-commit", log="off")
        out = self.run_logged("pre-commit")
        self.assertEqual(out.returncode, 1)
        self.assertEqual((out.stdout, out.stderr), (silent.stdout, silent.stderr))
        self.assertIn("agent-checks: .env: looks like a secret or credential file", out.stderr)
        [entry] = self.entries()
        self.assertEqual(entry["failed"], ["secret-file"])''',
     '''        self.stage({"a.notes-secret": "x\\n"})
        silent = self.run_logged("pre-commit", log="off")
        out = self.run_logged("pre-commit")
        self.assertEqual(out.returncode, 1)
        self.assertEqual((out.stdout, out.stderr), (silent.stdout, silent.stderr))
        self.assertIn("agent-checks: a.notes-secret: notes files", out.stderr)
        [entry] = self.entries()
        self.assertEqual(entry["failed"], [FIXTURE_CHECK])'''),
    ('''    def test_pre_push_to_default_lists_push_to_default(self):
        self.commit(self.repo, {"README.md": "# Example\\n"})
        head = self.git(self.repo, "rev-parse", "HEAD").strip()
        out = self.run_logged("pre-push", "origin", "https://github.com/example-owner/widgets.git",
                              stdin=f"refs/heads/main {head} refs/heads/main {'0' * 40}\\n")
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn("refusing to push to main", out.stderr)
        [entry] = self.entries()
        self.assertEqual(entry["event"], "pre-push")
        self.assertEqual(entry["checks"], ["staged-index", "push-to-default", "persistent-delete", "rules-dir", "secret-file", "skill-shell"])
        self.assertEqual(entry["failed"], ["push-to-default"])''',
     '''    def test_refused_pre_push_is_logged_as_pre_push(self):
        self.commit(self.repo, {"a.notes-secret": "x\\n"})
        head = self.git(self.repo, "rev-parse", "HEAD").strip()
        out = self.run_logged("pre-push", "origin", "https://github.com/example-owner/widgets.git",
                              stdin=f"refs/heads/feature {head} refs/heads/feature {'0' * 40}\\n")
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn("a.notes-secret: notes files", out.stderr)
        [entry] = self.entries()
        self.assertEqual(entry["event"], "pre-push")
        self.assertEqual(entry["checks"], [FIXTURE_CHECK])
        self.assertEqual(entry["failed"], [FIXTURE_CHECK])'''),
    ('''        self.assertEqual(entry["checks"], ["rules-dir", "secret-file", "skill-shell", "repo-local"])''',
     '''        self.assertEqual(entry["checks"], [FIXTURE_CHECK, "repo-local"])'''),
    ('''        self.commit(self.repo, {".env": "A=1\\n"})
        self.assertEqual(self.run_logged("ci", base, "HEAD").returncode, 1)''',
     '''        self.commit(self.repo, {"a.notes-secret": "x\\n"})
        self.assertEqual(self.run_logged("ci", base, "HEAD").returncode, 1)'''),
    ('''        for files, rc in (({"README.md": "# Example\\n"}, 0), ({".env": "A=1\\n"}, 1)):''',
     '''        for files, rc in (({"README.md": "# Example\\n"}, 0), ({"a.notes-secret": "x\\n"}, 1)):'''),
    # CheckIdTests: the runner's own ids, with no fleet check among them.
    ('''        calls = problem_calls(canonical_runner(RUNNER))
        self.assertGreaterEqual(len(calls), 7)''',
     '''        calls = problem_calls(canonical_runner(RUNNER))
        # The kit's runner has the machinery's own three (DECISIONS X9).
        self.assertGreaterEqual(len(calls), 3)'''),
    ('''        self.assertLessEqual({"rules-dir", "secret-file", "skill-shell", "staged-index", "push-to-default", "persistent-delete",
                              "repo-local"}, ids)''',
     '''        self.assertEqual({"repo-local", "runner-error", "tree-rev"}, ids)'''),
]


# ── test/run-tests.sh ────────────────────────────────────────────────────────
RUN_TESTS_EDITS = [
    # The delivered runner still runs in the delivered repo: its ci mode runs
    # the repo's own check over the pushed commit.
    ('''    # The delivered runner does its job in the delivered repo: a commit adding
    # a .env file fails the CI mode the workflow runs.
    local clone="$TEST_DIR/ac-clone"
    rm -rf "$clone"
    gattr_git clone -q "$bare/acorg_ac-none" "$clone"
    echo "TOKEN=x" > "$clone/.env"
    gattr_git -C "$clone" add .env
    gattr_git -C "$clone" commit -q -m "add env"
    rc=0; (cd "$clone" && bash "$runner" ci HEAD~1 HEAD) > "$out-ci.log" 2>&1 || rc=$?
    if [[ $rc -ne 0 ]] && grep -q '\\.env' "$out-ci.log"; then
        pass "agent-checks (sync): the delivered runner's ci mode refuses a committed .env"
    else
        fail "agent-checks (sync): the delivered runner's ci mode refuses a committed .env — exit $rc"
    fi''',
     '''    # The delivered runner does its job in the delivered repo: a commit adding
    # a file the repo's own check (.agent-checks/local.sh, a test fixture here:
    # the kit's runner ships no checks) refuses fails the CI mode the workflow runs.
    local clone="$TEST_DIR/ac-clone"
    rm -rf "$clone"
    gattr_git clone -q "$bare/acorg_ac-none" "$clone"
    mkdir -p "$clone/.agent-checks"
    printf '%s\\n' 'status=0' 'while IFS= read -r -d "" p; do case "$p" in *.draft) echo "local: $p is a draft" >&2; status=1 ;; esac; done' 'exit $status' \\
        > "$clone/.agent-checks/local.sh"
    echo "x" > "$clone/plan.draft"
    gattr_git -C "$clone" add .agent-checks/local.sh plan.draft
    gattr_git -C "$clone" commit -q -m "add draft"
    rc=0; (cd "$clone" && bash "$runner" ci HEAD~1 HEAD) > "$out-ci.log" 2>&1 || rc=$?
    if [[ $rc -ne 0 ]] && grep -q 'plan\\.draft' "$out-ci.log"; then
        pass "agent-checks (sync): the delivered runner's ci mode runs the repo's own check"
    else
        fail "agent-checks (sync): the delivered runner's ci mode runs the repo's own check — exit $rc"
    fi'''),
    # The persistent-delete check is not shipped; what stays is that pre-push
    # passes deletes and pushes it has no check for.
    ('''# pre-push refuses deleting a persistent/* branch and nothing else: deleting
# any other branch, and pushing to a persistent/* branch, still pass.
test_agent_checks_persistent_delete() {
    echo ""
    echo "=== Test: agent-checks pre-push refuses deleting a persistent/* branch ==="''',
     '''# pre-push with no check that refuses (the kit ships none, DECISIONS X9):
# deleting a branch and pushing to a persistent/* branch both pass.
test_agent_checks_persistent_delete() {
    echo ""
    echo "=== Test: agent-checks pre-push passes deletes and persistent/* pushes ==="'''),
    ('''    rc=0; (cd "$repo" && GIT_CONFIG_GLOBAL=/dev/null bash "$runner" pre-push origin https://example.com/r.git \\
        <<< "(delete) $zero refs/heads/persistent/results $sha") >/dev/null 2>&1 || rc=$?
    if [[ $rc -eq 1 ]]; then
        pass "agent-checks persistent: deleting persistent/results is refused"
    else
        fail "agent-checks persistent: deleting persistent/results is refused — exit $rc"
    fi
''', ''),
]


# ── test/test-on-hold-label.js ───────────────────────────────────────────────
ON_HOLD_EDITS = [
    ('''    if (process.env.ON_HOLD_MUTATION === 'policy' && name === 'agents-md/base.md') {
      source = source.replace('is paused by the owner', 'is ready for agent work');
    }
''', ''),
    ('''test('fleet rule protects issues and PRs, with owner control and passive status', () => {
  const body = prose(section('agents-md/base.md', 'Working in these repos'));
  includes(body, ['issue or PR labeled `on-hold` is paused by the owner', 'no commits, reviews, merges, closing or follow-ups that do it', 'read, cite and list it as on hold without asking again', 'A held PR stays open', 'Only the owner adds or removes the label', 'an agent the owner directs in that session']);
  assert.ok''',
     '''// The kit's base.md carries no rules (DECISIONS X9), so the on-hold rule's
// wording is not checked here; the payload's budget and mirror still are.
test('fleet payload stays under budget and matches its mirror', () => {
  assert.ok'''),
]


def apply(root: Path, ctx) -> None:
    _cases(root)
    _sub(root, "test/test_agent_check_lane.py", LANE_EDITS)
    _sub(root, "test/run-tests.sh", RUN_TESTS_EDITS)
    _sub(root, "test/test-on-hold-label.js", ON_HOLD_EDITS)
