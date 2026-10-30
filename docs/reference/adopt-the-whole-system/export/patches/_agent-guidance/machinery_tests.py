"""DECISIONS X9 and Content tiers, second half: the tests follow the content.

blank_content.py keeps the two core fleet checks (secret-file, skill-shell)
and the core guidance sections, and moves the opinionated rules-dir check to
the library. The tests below asserted on checks the kit does not ship in its
starter: rules-dir, whose library item puts its tests back when it is
installed, and the pre-push checks, which are not shipped at all. Every
change is listed in export/DECISIONS.md (X9, "Tests", and Content tiers)
with its reason.

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
# The lane's case table. Kept: the cases of the runner itself (events,
# ranges, the repo-local check) and of the two core fleet checks, secret-file
# and skill-shell. The two rules-dir cases move to the library with the
# opinionated rules-dir check, which appends them again when it is installed.
RULES_DIR_CASES = ["rules-dir-refused", "claude-dir-other-file-passes"]


def _cases(root: Path, lib) -> None:
    path = root / "test" / "fixtures" / "agent-checks-cases.jsonl"
    lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    ids = [json.loads(l)["id"] for l in lines]
    if not set(RULES_DIR_CASES) <= set(ids):
        raise SystemExit(f"machinery_tests: the case table lacks a rules-dir case: {RULES_DIR_CASES}")
    kept = [l for l, i in zip(lines, ids) if i not in RULES_DIR_CASES]
    moved = [l for l, i in zip(lines, ids) if i in RULES_DIR_CASES]
    path.write_text("".join(l + "\n" for l in kept), encoding="utf-8")
    lib.write_fragment("git-hook", "rules-dir", "agent-guidance", "cases.jsonl", "".join(l + "\n" for l in moved))
    lib.add_ops("git-hook", "rules-dir", "agent-guidance", [
        {"op": "append-lines", "file": "test/fixtures/agent-checks-cases.jsonl", "fragment": "cases.jsonl"},
    ])


# ── test/test_agent_check_lane.py ────────────────────────────────────────────
# The run log and the id tests list the checks the kit ships. The pre-push
# test refused a push to the default branch, a check the kit does not ship;
# it now pushes a feature branch whose commit the core secret-file check
# refuses, which still shows the pre-push event in the log.
LANE_EDITS = [
    ('''        self.assertEqual(entry["checks"], ["rules-dir", "secret-file", "skill-shell"])
        self.assertEqual(entry["failed"], [])''',
     '''        self.assertEqual(entry["checks"], ["secret-file", "skill-shell"])
        self.assertEqual(entry["failed"], [])'''),
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
        self.commit(self.repo, {".env": "A=1\\n"})
        head = self.git(self.repo, "rev-parse", "HEAD").strip()
        out = self.run_logged("pre-push", "origin", "https://github.com/example-owner/widgets.git",
                              stdin=f"refs/heads/feature {head} refs/heads/feature {'0' * 40}\\n")
        self.assertEqual(out.returncode, 1, out.stderr)
        self.assertIn("agent-checks: .env: looks like a secret", out.stderr)
        [entry] = self.entries()
        self.assertEqual(entry["event"], "pre-push")
        self.assertEqual(entry["checks"], ["secret-file", "skill-shell"])
        self.assertEqual(entry["failed"], ["secret-file"])'''),
    ('''        self.assertEqual(entry["checks"], ["rules-dir", "secret-file", "skill-shell", "repo-local"])''',
     '''        self.assertEqual(entry["checks"], ["secret-file", "skill-shell", "repo-local"])'''),
    ('''        calls = problem_calls(canonical_runner(RUNNER))
        self.assertGreaterEqual(len(calls), 7)''',
     '''        calls = problem_calls(canonical_runner(RUNNER))
        # The kit's runner: the machinery's own three, secret-file and skill-shell (Content tiers).
        self.assertGreaterEqual(len(calls), 5)'''),
    ('''        self.assertLessEqual({"rules-dir", "secret-file", "skill-shell", "staged-index", "push-to-default", "persistent-delete",
                              "repo-local"}, ids)''',
     '''        self.assertLessEqual({"secret-file", "skill-shell", "repo-local"}, ids)'''),
]

# What the rules-dir library item puts back in the same file.
LANE_RULES_DIR_OPS = [
    {"op": "replace", "file": "test/test_agent_check_lane.py", "count": 2,
     "old": '        self.assertEqual(entry["checks"], ["secret-file", "skill-shell"])\n',
     "new": '        self.assertEqual(entry["checks"], ["rules-dir", "secret-file", "skill-shell"])\n'},
    {"op": "replace", "file": "test/test_agent_check_lane.py",
     "old": '        self.assertEqual(entry["checks"], ["secret-file", "skill-shell", "repo-local"])\n',
     "new": '        self.assertEqual(entry["checks"], ["rules-dir", "secret-file", "skill-shell", "repo-local"])\n'},
    {"op": "replace", "file": "test/test_agent_check_lane.py",
     "old": '        self.assertLessEqual({"secret-file", "skill-shell", "repo-local"}, ids)\n',
     "new": '        self.assertLessEqual({"rules-dir", "secret-file", "skill-shell", "repo-local"}, ids)\n'},
]


# ── test/run-tests.sh ────────────────────────────────────────────────────────
RUN_TESTS_EDITS = [
    # The persistent-delete check is not shipped; what stays is that pre-push
    # passes deletes and pushes it has no check for.
    ('''# pre-push refuses deleting a persistent/* branch and nothing else: deleting
# any other branch, and pushing to a persistent/* branch, still pass.
test_agent_checks_persistent_delete() {
    echo ""
    echo "=== Test: agent-checks pre-push refuses deleting a persistent/* branch ==="''',
     '''# pre-push has no check of its own in the kit (DECISIONS X9, Content tiers):
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


def apply(root: Path, ctx) -> None:
    lib = ctx["library"]
    _cases(root, lib)
    _sub(root, "test/test_agent_check_lane.py", LANE_EDITS)
    lib.add_ops("git-hook", "rules-dir", "agent-guidance", LANE_RULES_DIR_OPS)
    _sub(root, "test/run-tests.sh", RUN_TESTS_EDITS)
