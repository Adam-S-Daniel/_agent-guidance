#!/usr/bin/env python3
"""The read-only real-work miner and its admission rules (#65, #98).

`scripts/mine_real_work.py` turns merged fleet pull requests into fixture
candidates (DESIGN.md "Real-work fixtures from merged pull requests"). These
tests drive it in-process against a fake `gh` answered from canned JSON, so
nothing touches the network; `prepare` runs against a throwaway local git
repository.

Discovered and run by test/run_tests.py; also runnable on its own with
`python3 test/issues/test_issue_real_work_miner.py`.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TEST_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = TEST_DIR.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import mine_real_work  # noqa: E402

#: A fake `gh` answered from `$FAKE_GH_DATA`. Every call is logged to
#: `$FAKE_GH_LOG`. It models a Claude Code cloud session (s27, 2026-10-06):
#: every GraphQL-backed verb (`repo view`, `pr list`, `pr diff`, `api graphql`)
#: exits 1 with the 403 such a session returns, and only REST reads under
#: `gh api` are answered; anything else exits 3.
FAKE_GH = r'''
import json
import os
import re
import sys

args = sys.argv[1:]
with open(os.environ["FAKE_GH_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\n")
with open(os.environ["FAKE_GH_DATA"], encoding="utf-8") as handle:
    data = json.load(handle)

GRAPHQL_403 = ("HTTP 403: GitHub GraphQL is not available from Claude Code sessions; "
               "use the REST API (gh api repos/{owner}/{repo}/...)")
DIFF_ACCEPT = "Accept: application/vnd.github.diff"


def fail(message, code=1):
    sys.stderr.write(message + "\n")
    raise SystemExit(code)


def not_found():
    sys.stdout.write('{"message":"Not Found","status":"404"}')
    fail("gh: Not Found (HTTP 404)")


def lines(rows):
    for row in rows:
        print(json.dumps(row))


if args[:2] in (["repo", "view"], ["pr", "list"], ["pr", "diff"], ["pr", "view"],
                ["issue", "view"], ["api", "graphql"]):
    fail(GRAPHQL_403)
if args[:1] != ["api"]:
    fail("fake gh: refused " + " ".join(args), 3)
flags, path, rest = set(), None, args[1:]
while rest:
    arg = rest.pop(0)
    if arg == "--paginate":
        flags.add(arg)
    elif arg in ("-H", "--jq"):
        flags.add((arg, rest.pop(0)))
    elif path is None and not arg.startswith("-"):
        path = arg
    else:
        fail("fake gh: refused " + " ".join(args), 3)
listing = flags == {"--paginate", ("--jq", ".[]")}
diff = flags == {("-H", DIFF_ACCEPT)}
if not (listing or diff or not flags):
    fail("fake gh: refused " + " ".join(args), 3)
path, _, query = (path or "").partition("?")
match = re.fullmatch(r"repos/([^/]+/[^/]+)(/.*)?", path)
if not match:
    fail("fake gh: refused " + " ".join(args), 3)
repo, tail = match.group(1), match.group(2) or ""
if tail == "" and not flags:
    view = data["repos"].get(repo)
    if view is None:
        not_found()
    print(json.dumps(view))
elif (tail == "/pulls" and not flags
      and re.fullmatch(r"state=closed&per_page=100&page=[1-9]\d*", query)):
    if repo not in data["prs"]:
        not_found()
    page = int(query.rsplit("=", 1)[1])
    print(json.dumps(data["prs"][repo][(page - 1) * 100:page * 100]))
elif re.fullmatch(r"/pulls/\d+/files", tail) and listing and query == "per_page=100":
    key = repo + "#" + tail.split("/")[2]
    lines(data["files"][key])
elif re.fullmatch(r"/pulls/\d+", tail) and not flags:
    number = int(tail.split("/")[2])
    row = next((r for r in data["prs"].get(repo, []) if r["number"] == number), None)
    if row is None:
        not_found()
    print(json.dumps(row))
elif re.fullmatch(r"/pulls/\d+", tail) and diff:
    key = repo + "#" + tail.split("/")[2]
    if key in data.get("diff_errors", {}):
        fail(data["diff_errors"][key])
    sys.stdout.write(data["diffs"][key])
elif re.fullmatch(r"/commits/[0-9a-f]{40}", tail) and not flags:
    sha = tail.split("/")[2]
    parents = data.get("parents", {}).get(sha, ["b" * 40, "c" * 40])
    if parents is None:
        not_found()
    print(json.dumps({"sha": sha, "parents": [{"sha": p} for p in parents]}))
elif re.fullmatch(r"/issues/\d+", tail) and not flags:
    number = int(tail.split("/")[2])
    issue = data.get("issues", {}).get(f"{repo}#{number}", "issue")
    if issue is None:
        not_found()
    if issue == "gone":
        sys.stdout.write('{"message":"PRIVATE ISSUE BODY","status":"410"}')
        fail("gh: PRIVATE ISSUE BODY (HTTP 410)")
    row = {"number": number, "html_url": f"https://github.com/{repo}/issues/{number}"}
    if issue == "pull":
        row["pull_request"] = {"url": "https://api.github.com/x"}
        row["html_url"] = f"https://github.com/{repo}/pull/{number}"
    print(json.dumps(row))
else:
    fail("fake gh: refused " + " ".join(args), 3)
'''

OWNER, OWNER_TWO = "example-adopter", "example-adopter-two"


def _view(owner, name, visibility="PUBLIC", default_branch="main"):
    """The REST `GET /repos/{owner}/{repo}` answer (visibility is lower case)."""
    return {"full_name": f"{owner}/{name}", "visibility": visibility.lower(),
            "fork": False, "archived": False, "default_branch": default_branch}


def _pr(number, *, head="feat/x", login="example-adopter", is_bot=False, body="Fix the bug.\n\nCloses #7",
        files=("scripts/tool.py", "test/test_tool.py"), labels=(), merged="2026-09-01T10:00:00Z",
        base_ref="main"):
    """One row of REST `GET /repos/{o}/{r}/pulls?state=closed`, with its files
    (served by the fake as `/pulls/{n}/files`) under the private key `_files`."""
    return {"number": number, "title": f"PR {number}", "body": body,
            "_files": [{"filename": p, "additions": 5, "deletions": 1} for p in files],
            "merge_commit_sha": f"{number:040x}", "head": {"ref": head},
            "base": {"ref": base_ref, "sha": "d" * 40},
            "user": {"login": login, "type": "Bot" if is_bot else "User"},
            "merged_at": merged, "labels": [{"name": n} for n in labels],
            "html_url": f"https://github.com/example/r/pull/{number}"}


DIFF = ("diff --git a/scripts/tool.py b/scripts/tool.py\n--- a/scripts/tool.py\n"
        "+++ b/scripts/tool.py\n@@ -1 +1,2 @@\n x = 1\n"
        "+    return parse_listing(page, truncated=True)\n"
        "diff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n"
        "@@ -1 +1,2 @@\n+The listing parser now reports truncation.\n")


def _junit(cases):
    rows = []
    for name, outcome, message in cases:
        inner = ""
        if outcome == "fail":
            inner = f'<failure message="{message}"/>'
        rows.append(f'<testcase classname="suite" name="{name}">{inner}</testcase>')
    return "<testsuites><testsuite>" + "".join(rows) + "</testsuite></testsuites>"


class _MinerCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(f"#!{sys.executable}\n" + FAKE_GH, encoding="utf-8")
        gh.chmod(0o755)
        self.log = self.tmp / "gh.log"
        self.data_path = self.tmp / "gh-data.json"
        self.env = mock.patch.dict(os.environ, {
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "FAKE_GH_LOG": str(self.log), "FAKE_GH_DATA": str(self.data_path)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.out = self.tmp / "out" / "candidates.json"

    def registry(self, fleet, owners="example-adopter example-adopter-two"):
        root = self.tmp / "agent-guidance"
        (root / ".github/workflows").mkdir(parents=True, exist_ok=True)
        (root / "repos.yml").write_text(
            "exclude: []\ncron_coverage:\n  fleet:\n"
            + "".join(f"    - {name}\n" for name in fleet), encoding="utf-8")
        (root / ".github/workflows/sync.yml").write_text(
            "jobs:\n  sync:\n    steps:\n      - name: Sync\n        env:\n"
            f'          SYNC_OWNERS: "{owners}"\n        run: scripts/sync.sh\n',
            encoding="utf-8")
        return root / "repos.yml"

    def gh_data(self, repos, prs, diffs=None, parents=None, diff_errors=None, issues=None):
        diffs, files, listed = dict(diffs or {}), {}, {}
        for repo, rows in prs.items():
            listed[repo] = []
            for row in rows:
                row = dict(row)
                files[f"{repo}#{row['number']}"] = row.pop("_files", [])
                listed[repo].append(row)
                diffs.setdefault(f"{repo}#{row['number']}", DIFF)
        self.data_path.write_text(json.dumps({"repos": repos, "prs": listed, "files": files,
                                              "diffs": diffs, "parents": parents or {},
                                              "diff_errors": diff_errors or {},
                                              "issues": issues or {}}),
                                  encoding="utf-8")

    def run_main(self, *argv):
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            rc = mine_real_work.main(list(argv))
        return rc, err.getvalue()

    def mine(self, registry):
        rc, err = self.run_main("mine", "--registry", str(registry), "--out", str(self.out))
        self.assertEqual(rc, 0, err)
        self.stderr = err
        return json.loads(self.out.read_text(encoding="utf-8"))

    def calls(self):
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]


class TestFleetEnumeration(_MinerCase):
    def _both_owner_world(self):
        self.gh_data(
            repos={f"{OWNER}/skills-evals": _view(OWNER, "skills-evals"),
                   f"{OWNER_TWO}/example-site-b": _view(OWNER_TWO, "example-site-b"),
                   f"{OWNER}/repo-settings": _view(OWNER, "repo-settings", "PRIVATE"),
                   # The rename redirect DESIGN.md §1.1 measured: one repo, two names.
                   f"{OWNER}/_agent-guidance": _view(OWNER, "_agent-guidance"),
                   f"{OWNER_TWO}/_agent-guidance": _view(OWNER, "_agent-guidance")},
            prs={f"{OWNER}/skills-evals": [_pr(1)], f"{OWNER_TWO}/example-site-b": [_pr(2)],
                 f"{OWNER}/_agent-guidance": [_pr(3)], f"{OWNER}/repo-settings": [_pr(4)]})
        return self.registry(["skills-evals", "example-site-b", "repo-settings", "_agent-guidance"])

    def test_both_owners_are_enumerated_from_the_registry(self):
        doc = self.mine(self._both_owner_world())
        self.assertEqual(doc["owners"], [OWNER, OWNER_TWO])
        repos = sorted(c["repo"] for c in doc["candidates"])
        self.assertEqual(repos, [f"{OWNER_TWO}/example-site-b", f"{OWNER}/_agent-guidance",
                                 f"{OWNER}/skills-evals"])
        probed = {c[1].removeprefix("repos/") for c in self.calls()
                  if len(c) == 2 and c[1].count("/") == 2}
        self.assertEqual(probed, {f"{o}/{n}" for o in (OWNER, OWNER_TWO) for n in
                                  ("skills-evals", "example-site-b", "repo-settings",
                                   "_agent-guidance")})

    def test_a_one_owner_registry_fails_instead_of_shrinking_the_fleet(self):
        self._both_owner_world()
        registry = self.registry(["skills-evals", "example-site-b", "repo-settings",
                                  "_agent-guidance"], owners=OWNER)
        rc, err = self.run_main("mine", "--registry", str(registry), "--out", str(self.out))
        self.assertEqual(rc, 2)
        self.assertIn("example-site-b", err)
        self.assertFalse(self.out.exists())

    def test_a_private_repo_is_skipped_and_never_listed(self):
        doc = self.mine(self._both_owner_world())
        self.assertEqual(doc["skipped"], [{"repo": f"{OWNER}/repo-settings", "reason": "not-public"}])
        listed = [c[1] for c in self.calls() if "/pulls?" in " ".join(c)]
        self.assertTrue(listed)
        self.assertFalse([c for c in listed if "repo-settings" in c])

    def test_only_rest_reads_reach_gh(self):
        # s27 (2026-10-06): a Claude Code cloud session answers every GitHub
        # GraphQL request with HTTP 403, and `gh repo view`, `gh pr list` and
        # `gh pr diff` all go through GraphQL, so the routine's scaffold mode
        # died at its first `gh repo view`. Only `gh api` REST reads remain.
        self.mine(self._both_owner_world())
        rest = re.compile(r"^repos/[^/]+/[^/]+(/pulls\?state=closed&per_page=100"
                          r"&page=[1-9]\d*|/pulls/\d+(/files\?per_page=100)?|/commits/[0-9a-f]{40}"
                          r"|/issues/\d+)?$")
        shapes = set()
        for call in self.calls():
            self.assertEqual(call[0], "api", call)
            self.assertNotIn("graphql", call)
            path = [a for a in call[1:] if a.startswith("repos/")]
            self.assertEqual(len(path), 1, call)
            self.assertRegex(path[0], rest)
            rest_flags = [a for a in call[1:] if a != path[0]]
            self.assertIn(rest_flags, ([], ["--paginate", "--jq", ".[]"],
                                       ["-H", "Accept: application/vnd.github.diff"]), call)
            shapes.add(tuple(rest_flags))
        self.assertEqual(len(shapes), 3, "repo, list and diff reads all ran")

    def test_a_repo_whose_pull_requests_404_is_skipped_with_a_warning(self):
        self.gh_data(repos={f"{OWNER}/skills-evals": _view(OWNER, "skills-evals"),
                            f"{OWNER}/GHA-bench": _view(OWNER, "GHA-bench")},
                     prs={f"{OWNER}/skills-evals": [_pr(1)]})
        doc = self.mine(self.registry(["GHA-bench", "skills-evals"]))
        self.assertEqual([c["repo"] for c in doc["candidates"]], [f"{OWNER}/skills-evals"])
        self.assertEqual(doc["skipped"], [{"repo": f"{OWNER}/GHA-bench", "reason": "pr-list-404"}])
        self.assertIn("GHA-bench: pull requests not readable", self.stderr)

    def test_out_inside_the_main_checkout_is_refused_from_a_worktree(self):
        main = self.tmp / "main"
        main.mkdir()
        git = ["git", "-c", "user.name=seed", "-c", "user.email=seed@example.com",
               "-c", "commit.gpgsign=false"]
        subprocess.run([*git, "-C", str(main), "init", "-q"], check=True)
        subprocess.run([*git, "-C", str(main), "commit", "-q", "--allow-empty", "-m", "seed"],
                       check=True)
        worktree = self.tmp / "main-wt"
        subprocess.run([*git, "-C", str(main), "worktree", "add", "-q", str(worktree)],
                       check=True, capture_output=True)
        target = main / "candidates.json"
        with mock.patch.object(mine_real_work, "REPO_ROOT", worktree.resolve()):
            rc, err = self.run_main("mine", "--registry", str(self.registry(["x"])),
                                    "--out", str(target))
        self.assertEqual(rc, 2)
        self.assertIn("never writes the repo", err)
        self.assertFalse(target.exists())

    def test_out_inside_the_repo_is_refused_and_nothing_is_written(self):
        registry = self._both_owner_world()
        inside = REPO_ROOT / "candidates.json"
        rc, err = self.run_main("mine", "--registry", str(registry), "--out", str(inside))
        self.assertEqual(rc, 2)
        self.assertIn("never writes the repo", err)
        self.assertFalse(inside.exists())
        self.assertFalse(self.log.exists(), "refused before any gh call")


class TestCandidateFilters(_MinerCase):
    def _mine(self, prs, diffs=None, parents=None):
        self.gh_data(repos={f"{OWNER}/example-site-platform": _view(OWNER, "example-site-platform")},
                     prs={f"{OWNER}/example-site-platform": prs}, diffs=diffs, parents=parents)
        return self.mine(self.registry(["example-site-platform"]))

    def test_bot_prs_are_excluded_by_head_ref_and_author(self):
        doc = self._mine([
            _pr(1), _pr(2, head="dependabot/npm/yaml-2"), _pr(3, head="cms/posts/hello"),
            _pr(4, head="scaffold/example-site-platform-693"), _pr(5, head="agents-md-sync"),
            _pr(6, login="app/github-actions"), _pr(7, login="renovate[bot]"),
            _pr(8, login="someone", is_bot=True)])
        self.assertEqual([c["pr"] for c in doc["candidates"]], [1])
        self.assertEqual(doc["summary"][0]["bot"], 7)

    def test_on_hold_unreplayable_and_bodiless_prs_are_excluded(self):
        doc = self._mine([
            _pr(1), _pr(2, labels=("on-hold",)), _pr(3, files=("scripts/tool.py",)),
            _pr(4, files=("test/test_tool.py", "README.md")), _pr(5, body="  ")])
        self.assertEqual([c["pr"] for c in doc["candidates"]], [1])
        summary = doc["summary"][0]
        self.assertEqual((summary["on_hold"], summary["not_replayable"], summary["no_task_text"]),
                         (1, 2, 1))

    def test_candidate_records_merge_date_base_and_the_body_as_task_text(self):
        body = "The poller drops the last page.\n\n```js\nfetchAll()\n```"
        cand = self._mine([_pr(9, body=body, merged="2025-12-31T23:00:00Z")])["candidates"][0]
        self.assertEqual(cand["merged_at"], "2025-12-31T23:00:00Z")
        self.assertEqual(cand["merge_sha"], f"{9:040x}")
        self.assertEqual((cand["base_sha"], cand["base_from"]), ("b" * 40, "merge-first-parent"))
        self.assertEqual(cand["task_text"], body)
        self.assertEqual(cand["spec_style"], "sketch")
        self.assertEqual((cand["test_files"], cand["source_files"]),
                         (["test/test_tool.py"], ["scripts/tool.py"]))
        self.assertEqual(cand["key"], "example-adopter__example-site-platform__9")
        self.assertNotIn("subject", cand)

    def test_a_single_parent_merge_takes_the_prs_base_ref_oid(self):
        # A rebase merge's first parent is the previous rebased commit, not the base.
        cand = self._mine([_pr(9)], parents={f"{9:040x}": ["e" * 40]})["candidates"][0]
        self.assertEqual((cand["base_sha"], cand["base_from"]), ("d" * 40, "pr-base-ref-oid"))

    def test_a_pr_without_a_readable_merge_commit_is_skipped_with_a_warning(self):
        no_commit = _pr(2)
        no_commit["merge_commit_sha"] = None
        doc = self._mine([_pr(1), no_commit, _pr(3)], parents={f"{3:040x}": None})
        self.assertEqual([c["pr"] for c in doc["candidates"]], [1])
        self.assertEqual(doc["summary"][0]["no_merge_commit"], 2)
        self.assertIn("#2: no readable merge commit", self.stderr)
        self.assertIn("#3: no readable merge commit", self.stderr)

    def _mine_with_diff_error(self, number, message):
        repo = f"{OWNER}/skills-evals"
        self.gh_data(repos={repo: _view(OWNER, "skills-evals")},
                     prs={repo: [_pr(1), _pr(number), _pr(3)]},
                     diff_errors={f"{repo}#{number}": message})
        return self.registry(["skills-evals"])

    def test_a_diff_github_will_not_render_skips_that_pr_and_mining_continues(self):
        # skills-evals#311 (300+ files): HTTP 406 aborted the whole mine. The
        # REST read puts the status last (measured, gh 2.95.0, 2026-10-06);
        # `gh pr diff` put it first.
        messages = [
            (kind, form.format(kind=kind))
            for kind in ("files (300)", "lines (20000)")
            for form in (
                "gh: Sorry, the diff exceeded the maximum number of {kind}. Consider using "
                "'List pull requests files' API or locally cloning the repository instead. "
                "(HTTP 406)",
                "could not find pull request diff: HTTP 406: Sorry, the diff exceeded the "
                "maximum number of {kind}. Consider using 'List pull requests files' API or "
                "locally cloning the repository instead. "
                "(https://api.github.com/repos/o/r/pulls/2)")]
        for kind, message in messages:
            with self.subTest(kind=kind, message=message[:12]):
                registry = self._mine_with_diff_error(2, message)
                doc = self.mine(registry)
                self.assertEqual([c["pr"] for c in doc["candidates"]], [1, 3])
                self.assertEqual(doc["summary"][0]["diff_too_large"], 1)
                self.assertIn("skills-evals#2: diff too large", self.stderr)
                self.assertNotIn("Consider using", self.stderr)
                self.assertNotIn("api.github.com", self.stderr)

    def test_an_unknown_diff_failure_still_fails_loudly_without_echoing_the_body(self):
        for message in ("HTTP 500: server secret-body-text",
                        "HTTP 406: Not Acceptable secret-body-text",
                        "diff exceeded the maximum number of lines secret-body-text"):
            with self.subTest(message=message):
                registry = self._mine_with_diff_error(2, message)
                rc, err = self.run_main("mine", "--registry", str(registry), "--out", str(self.out))
                self.assertEqual(rc, 2)
                self.assertIn("pulls/2 failed (exit 1)", err)
                self.assertNotIn("secret-body-text", err)
                self.assertFalse(self.out.exists())

    def test_answer_leak_flags_a_body_that_quotes_an_added_line(self):
        doc = self._mine([
            _pr(1, body="Make it call `return parse_listing(page, truncated=True)` instead."),
            _pr(2, body="The listing drops its last page."),
            _pr(3, body="Docs: The listing parser now reports truncation.")])
        leak = {c["pr"]: c["answer_leak"] for c in doc["candidates"]}
        self.assertEqual(leak[1], {"flag": True,
                                   "quoted_lines": ["return parse_listing(page, truncated=True)"]})
        self.assertEqual(leak[2], {"flag": False, "quoted_lines": []})
        self.assertFalse(leak[3]["flag"], "a quoted doc line is not the answer")


    def test_closed_unmerged_prs_are_dropped_and_the_limit_counts_merged_ones(self):
        # REST lists closed pull requests; `gh pr list --state merged` did the
        # filtering server-side, so the miner now drops the unmerged ones and
        # applies --limit to what is left, newest first as listed.
        closed = _pr(2)
        closed["merged_at"] = None
        self.gh_data(repos={f"{OWNER}/example-site-platform": _view(OWNER, "example-site-platform")},
                     prs={f"{OWNER}/example-site-platform": [_pr(4), closed, _pr(3), _pr(1)]})
        rc, err = self.run_main("mine", "--registry", str(self.registry(["example-site-platform"])),
                                "--out", str(self.out), "--limit", "2")
        self.assertEqual(rc, 0, err)
        doc = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual([c["pr"] for c in doc["candidates"]], [4, 3])
        self.assertEqual(doc["summary"][0]["merged"], 2)

    def test_limit_stops_paging_after_collecting_merged_prs(self):
        repo = f"{OWNER}/example-site-platform"
        rows = [_pr(n, merged=None, head="dependabot/x") for n in range(1, 100)]
        rows += [_pr(100), _pr(101), _pr(102)]
        self.gh_data(repos={repo: _view(OWNER, "example-site-platform")}, prs={repo: rows})
        registry = self.registry(["example-site-platform"])
        for limit, expected, pages in ((1, 1, [1]), (2, 2, [1, 2]), (9, 3, [1, 2])):
            with self.subTest(limit=limit):
                self.log.write_text("")
                rc, err = self.run_main("mine", "--registry", str(registry),
                                        "--out", str(self.out), "--limit", str(limit))
                self.assertEqual(rc, 0, err)
                doc = json.loads(self.out.read_text())
                self.assertEqual(doc["summary"][0]["merged"], expected)
                self.assertEqual([c["pr"] for c in doc["candidates"]],
                                 [100, 101, 102][:expected])
                self.assertEqual([c for c in self.calls() if "/pulls?" in c[1]],
                                 [["api", f"repos/{repo}/pulls?state=closed&per_page=100&page={n}"]
                                  for n in pages])

    def test_full_unmerged_page_exhausts_on_an_empty_page(self):
        repo = f"{OWNER}/example-site-platform"
        self.gh_data(repos={repo: _view(OWNER, "example-site-platform")},
                     prs={repo: [_pr(n, merged=None) for n in range(1, 101)]})
        doc = self.mine(self.registry(["example-site-platform"]))
        self.assertEqual(doc["summary"][0]["merged"], 0)
        self.assertEqual([c for c in self.calls() if "/pulls?" in c[1]],
                         [["api", f"repos/{repo}/pulls?state=closed&per_page=100&page={n}"]
                          for n in (1, 2)])

    def test_churn_and_files_come_from_the_files_listing(self):
        many = [f"scripts/m{i}.py" for i in range(150)] + ["test/test_tool.py"]
        doc = self._mine([_pr(1), _pr(2, files=many)])
        cand = {c["pr"]: c for c in doc["candidates"]}
        self.assertEqual(cand[1]["churn"], 12)
        self.assertEqual((cand[1]["test_files"], cand[1]["source_files"]),
                         (["test/test_tool.py"], ["scripts/tool.py"]))
        # The REST listing pages past `gh pr list`'s 100-file cap.
        self.assertEqual(len(cand[2]["source_files"]), 150)
        self.assertFalse(cand[2]["files_truncated"])


class TestSingleCandidate(_MinerCase):
    KEY = f"{OWNER}__example-site-platform__760"

    def _world(self, prs=None, repos=None):
        self.gh_data(repos=repos or {f"{OWNER}/example-site-platform": _view(OWNER, "example-site-platform"),
                                     f"{OWNER}/other": _view(OWNER, "other"),
                                     f"{OWNER_TWO}/example-site-platform": _view(OWNER, "example-site-platform")},
                     prs={f"{OWNER}/example-site-platform": prs or [_pr(759), _pr(760)],
                          f"{OWNER}/other": [_pr(1)]})
        return self.registry(["example-site-platform", "other"])

    def _run(self, registry, key=None):
        rc, err = self.run_main("mine", "--registry", str(registry), "--out", str(self.out),
                                "--candidate", key or self.KEY)
        return rc, err

    def _refused(self, registry, fragment, key=None):
        rc, err = self._run(registry, key)
        self.assertEqual(rc, 2, err)
        self.assertIn(fragment, err)
        self.assertFalse(self.out.exists())

    def test_a_valid_key_yields_exactly_that_candidate_as_full_mining_would(self):
        registry = self._world()
        rc, err = self._run(registry)
        self.assertEqual(rc, 0, err)
        doc = json.loads(self.out.read_text(encoding="utf-8"))
        full = self.mine(registry)
        want = next(c for c in full["candidates"] if c["key"] == self.KEY)
        self.assertEqual(doc["candidates"], [want])
        self.assertEqual(doc["owners"], [OWNER, OWNER_TWO])
        self.assertEqual(doc["skipped"], [])
        self.assertEqual(doc["summary"][0]["merged"], 1)
        self.assertEqual(doc["summary"][0]["candidates"], 1)

    def test_only_the_candidates_repository_is_read(self):
        rc, err = self._run(self._world())
        self.assertEqual(rc, 0, err)
        repo_calls = {a for c in self.calls() for a in c if a.startswith("repos/")}
        self.assertEqual(repo_calls, {
            f"repos/{OWNER}/example-site-platform",
            f"repos/{OWNER}/example-site-platform/pulls/760",
            f"repos/{OWNER}/example-site-platform/pulls/760/files?per_page=100",
            f"repos/{OWNER}/example-site-platform/commits/{760:040x}",
            f"repos/{OWNER}/example-site-platform/issues/7"})
        self.assertFalse(any("other" in " ".join(c) or OWNER_TWO in " ".join(c)
                             for c in self.calls()))

    def test_an_owner_outside_sync_owners_is_refused_before_any_gh_call(self):
        registry = self._world()
        self._refused(registry, "is not in SYNC_OWNERS", key="stranger__example-site-platform__760")
        self.assertFalse(self.log.exists())

    def test_a_name_outside_the_fleet_is_refused_before_any_gh_call(self):
        registry = self._world()
        self._refused(registry, "is not in the fleet", key=f"{OWNER}__unlisted__760")
        self.assertFalse(self.log.exists())

    def test_a_malformed_key_is_refused(self):
        registry = self._world()
        for key in ("example-site-platform", f"{OWNER}__example-site-platform__0", f"{OWNER}__example-site-platform__x"):
            self._refused(registry, "is not a miner key", key=key)

    def test_a_redirected_repository_is_refused(self):
        registry = self._world(repos={f"{OWNER}/example-site-platform": _view(OWNER_TWO, "renamed")})
        self._refused(registry, "renamed or redirected")

    def test_a_private_repository_is_refused(self):
        registry = self._world(repos={f"{OWNER}/example-site-platform":
                                      _view(OWNER, "example-site-platform", "PRIVATE")})
        self._refused(registry, "is not public")

    def test_an_unmerged_pull_request_is_refused(self):
        registry = self._world(prs=[_pr(760, merged=None)])
        self._refused(registry, "is not merged")

    def test_a_missing_pull_request_is_refused(self):
        registry = self._world(prs=[_pr(759)])
        self._refused(registry, "760 not found")

    def test_a_filtered_pull_request_yields_no_candidate_and_exit_zero(self):
        registry = self._world(prs=[_pr(760, login="renovate[bot]")])
        rc, err = self._run(registry)
        self.assertEqual(rc, 0, err)
        doc = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(doc["candidates"], [])
        self.assertEqual(doc["summary"][0]["bot"], 1)


class TestClosingIssues(_MinerCase):
    REPO = f"{OWNER}/example-site-platform"

    def test_closing_keywords_are_read_as_github_reads_them(self):
        refs = mine_real_work.closing_refs
        cases = {
            "Closes #7": [7], "fixes: #8": [8], "RESOLVED #9": [9], "Fix #1 and close #2": [1, 2],
            "Closes #1, #2": [1], "Fixes #3. Fixes #3": [3],
            "Closes example-adopter/example-site-platform#5": [5], "closes example-adopter/Example-Site-Platform#6": [6],
            "Fixes https://github.com/example-adopter/example-site-platform/issues/11": [11],
            "Closes example-adopter/skills-evals#5": [],
            "Fixes https://github.com/example-adopter/skills-evals/issues/11": [],
            "Refs #3": [], "Part of #4": [], "prefixes #5": [], "Closes #": [],
            "Closes #12abc": [], "`Resolves #14` in a code span": [14],
        }
        for body, want in cases.items():
            with self.subTest(body=body):
                self.assertEqual(refs(body, self.REPO), want)

    def test_only_real_issues_in_this_repo_are_recorded(self):
        # acsi-agentskills#40 closes #24 then #12; GraphQL listed [12, 24].
        body = "Closes #10, closes #8, closes #9 and fixes #7."
        self.gh_data(repos={self.REPO: _view(OWNER, "example-site-platform")},
                     prs={self.REPO: [_pr(1, body=body)]},
                     issues={f"{self.REPO}#8": "pull", f"{self.REPO}#9": None})
        doc = self.mine(self.registry(["example-site-platform"]))
        self.assertEqual(doc["candidates"][0]["closing_issues"], [7, 10])

    def test_deleted_closing_issue_is_skipped_without_exposing_its_body(self):
        self.gh_data(repos={self.REPO: _view(OWNER, "example-site-platform")},
                     prs={self.REPO: [_pr(1, body="Closes #7; fixes #8"), _pr(2)]},
                     issues={f"{self.REPO}#8": "gone"})
        doc = self.mine(self.registry(["example-site-platform"]))
        self.assertEqual([c["closing_issues"] for c in doc["candidates"]], [[7], [7]])
        self.assertNotIn("PRIVATE ISSUE BODY", self.stderr)

    def test_a_pull_request_into_another_branch_closes_nothing(self):
        self.gh_data(repos={self.REPO: _view(OWNER, "example-site-platform")},
                     prs={self.REPO: [_pr(1), _pr(2, base_ref="release")]})
        doc = self.mine(self.registry(["example-site-platform"]))
        self.assertEqual({c["pr"]: c["closing_issues"] for c in doc["candidates"]},
                         {1: [7], 2: []})
        issue_reads = [c for c in self.calls() if "/issues/" in c[-1]]
        self.assertEqual(len(issue_reads), 1, "the release PR's #7 is never looked up")


class TestAdmission(unittest.TestCase):
    CAND = {"key": "k", "task_text": "Report a truncated listing.",
            "source_files": ["scripts/health.js"], "merged_at": "2026-09-01T00:00:00Z"}
    RUN = {"red_secs": 4, "green_secs": 4, "timed_out": False}

    @staticmethod
    def side(**cases):
        return {name: {"outcome": outcome, "message": message}
                for name, (outcome, message) in cases.items()}

    def test_fixture_8_environmental_failures_are_excluded_from_fail_to_pass(self):
        env = {f"env{i}": ("fail", "node_modules/yaml is missing") for i in range(8)}
        red = self.side(partial_a=("fail", "AssertionError: marker absent"),
                        partial_b=("fail", "AssertionError: marker absent"),
                        steady=("pass", ""), **env)
        green = self.side(partial_a=("pass", ""), partial_b=("pass", ""), steady=("pass", ""), **env)
        row = mine_real_work.admit(self.CAND, red, green, self.RUN)
        self.assertTrue(row["admitted"], row)
        self.assertEqual(row["fail_to_pass"], ["partial_a", "partial_b"])
        self.assertEqual(row["pass_to_pass"], ["steady"])
        self.assertEqual(len(row["environmental"]), 8)

    def test_a_test_that_fails_on_the_merge_too_is_environmental_whatever_it_says(self):
        red = self.side(fixed=("fail", "AssertionError"), both=("fail", "AssertionError: 1 != 2"))
        green = self.side(fixed=("pass", ""), both=("fail", "AssertionError: 1 != 2"))
        row = mine_real_work.admit(self.CAND, red, green, self.RUN)
        self.assertEqual((row["fail_to_pass"], row["environmental"]), (["fixed"], ["both"]))

    def test_a_candidate_whose_red_failures_are_all_environmental_is_rejected(self):
        red = self.side(a=("fail", "node_modules/yaml is missing"),
                        b=("fail", "ModuleNotFoundError: No module named 'yaml'"),
                        c=("fail", "getaddrinfo ENOTFOUND registry.example.com"))
        green = self.side(a=("fail", "node_modules/yaml is missing"), b=("pass", ""), c=("pass", ""))
        row = mine_real_work.admit(self.CAND, red, green, self.RUN)
        self.assertFalse(row["admitted"])
        self.assertEqual(row["reasons"], ["environmental"])
        self.assertEqual(row["environmental"], ["a", "b", "c"])

    def test_fixture_4_interface_coupled_red_failure_is_rejected(self):
        red = self.side(health=("fail", "TypeError: health.isListingTruncated is not a function"),
                        other=("fail", "AssertionError: expected 3"))
        green = self.side(health=("pass", ""), other=("pass", ""))
        row = mine_real_work.admit(self.CAND, red, green, self.RUN)
        self.assertFalse(row["admitted"])
        self.assertEqual(row["reasons"], ["interface-coupled"])
        self.assertEqual(row["interface_coupled"], [{"test": "health", "name": "isListingTruncated"}])

    def test_interface_named_by_the_task_text_is_admitted(self):
        cand = dict(self.CAND, task_text="Add isListingTruncated(page) to the health module.")
        red = self.side(health=("fail", "TypeError: health.isListingTruncated is not a function"))
        row = mine_real_work.admit(cand, red, self.side(health=("pass", "")), self.RUN)
        self.assertTrue(row["admitted"], row)
        self.assertEqual(row["fail_to_pass"], ["health"])

    def test_python_and_module_shapes_of_interface_coupling(self):
        cases = {
            "imp": "ImportError: cannot import name 'parse_listing' from 'tool'",
            "attr": "AttributeError: module 'tool' has no attribute 'parse_listing'",
            "esm": "SyntaxError: The requested module './health.js' does not provide an export named 'probe'",
            "rel": "Error: Cannot find module './listing-helper'",
            "ours": "ModuleNotFoundError: No module named 'scripts.health'",
        }
        for name, message in cases.items():
            with self.subTest(name=name):
                row = mine_real_work.admit(self.CAND, self.side(t=("fail", message)),
                                           self.side(t=("pass", "")), self.RUN)
                self.assertFalse(row["admitted"], row)
                self.assertIn("interface-coupled", row["reasons"], row)

    def test_over_the_cap_is_rejected(self):
        red, green = self.side(t=("fail", "AssertionError")), self.side(t=("pass", ""))
        for run in ({"red_secs": 85, "green_secs": 4}, {"red_secs": 4, "green_secs": 4, "timed_out": True}):
            with self.subTest(run=run):
                row = mine_real_work.admit(self.CAND, red, green, run)
                self.assertEqual(row["reasons"], ["over-cap"])

    def test_a_repeated_test_id_cannot_hide_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "red.xml"
            path.write_text(_junit([("a", "fail", "AssertionError"), ("a", "pass", ""),
                                    ("b", "pass", ""), ("b", "fail", "AssertionError")]),
                            encoding="utf-8")
            cases = mine_real_work.read_junit(path)
        self.assertEqual({k: v["outcome"] for k, v in cases.items()},
                         {"suite::a": "fail", "suite::b": "fail"})

    def test_malformed_junit_rejects_only_that_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "candidates.json").write_text(json.dumps({"candidates": [
                dict(self.CAND, key="broken"), dict(self.CAND, key="fine")]}), encoding="utf-8")
            for key, red in (("broken", "<testsuites><testsuite><testcase name="),
                             ("fine", _junit([("a", "fail", "AssertionError")]))):
                where = tmp / "rg" / key
                where.mkdir(parents=True)
                (where / "red.xml").write_text(red, encoding="utf-8")
                (where / "green.xml").write_text(_junit([("a", "pass", "")]), encoding="utf-8")
                (where / "run.json").write_text(json.dumps(self.RUN), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                rc = mine_real_work.main(["admit", "--candidates", str(tmp / "candidates.json"),
                                          "--results", str(tmp / "rg"), "--out", str(tmp / "a.json")])
            self.assertEqual(rc, 0)
            rows = {r["key"]: r for r in json.loads((tmp / "a.json").read_text())["admissions"]}
        self.assertEqual((rows["broken"]["admitted"], rows["broken"]["reasons"]),
                         (False, ["unreadable-results"]))
        self.assertTrue(rows["fine"]["admitted"], rows["fine"])

    def test_admit_cli_reads_junit_and_reports_unrun_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "candidates.json").write_text(json.dumps({"candidates": [
                dict(self.CAND, key="ran"), dict(self.CAND, key="unrun")]}), encoding="utf-8")
            where = tmp / "rg" / "ran"
            where.mkdir(parents=True)
            (where / "red.xml").write_text(_junit([("a", "fail", "AssertionError: 1 != 2"),
                                                   ("b", "pass", "")]), encoding="utf-8")
            (where / "green.xml").write_text(_junit([("a", "pass", ""), ("b", "pass", "")]),
                                             encoding="utf-8")
            (where / "run.json").write_text(json.dumps(self.RUN), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                rc = mine_real_work.main(["admit", "--candidates", str(tmp / "candidates.json"),
                                          "--results", str(tmp / "rg"), "--out", str(tmp / "a.json")])
            self.assertEqual(rc, 0)
            doc = json.loads((tmp / "a.json").read_text(encoding="utf-8"))
            self.assertEqual(doc["not_run"], ["unrun"])
            self.assertEqual(doc["admissions"][0]["fail_to_pass"], ["suite::a"])
            self.assertEqual(doc["admissions"][0]["pass_to_pass"], ["suite::b"])
            self.assertTrue(doc["admissions"][0]["admitted"])


class TestPrepare(unittest.TestCase):
    def test_red_tree_is_base_with_the_merge_tests_laid_over(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            clone = tmp / "clone"
            clone.mkdir()

            def git(*args):
                return subprocess.run(
                    ["git", "-C", str(clone), "-c", "user.name=seed",
                     "-c", "user.email=seed@example.com", "-c", "commit.gpgsign=false", *args],
                    check=True, capture_output=True, text=True).stdout.strip()

            git("init", "-q")
            (clone / "test").mkdir()
            (clone / "tool.py").write_text("BUG = True\n", encoding="utf-8")
            (clone / "test/test_tool.py").write_text("old\n", encoding="utf-8")
            git("add", "-A")
            git("commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            (clone / "tool.py").write_text("BUG = False\n", encoding="utf-8")
            (clone / "test/test_tool.py").write_text("new\n", encoding="utf-8")
            git("commit", "-qam", "fix")
            merge = git("rev-parse", "HEAD")
            out = tmp / "rg"
            with contextlib.redirect_stdout(io.StringIO()):
                rc = mine_real_work.main(["prepare", "--clone", str(clone), "--base", base,
                                          "--merge", merge, "--test-file", "test/test_tool.py",
                                          "--out", str(out)])
            self.assertEqual(rc, 0)
            self.assertEqual((out / "red/tool.py").read_text(encoding="utf-8"), "BUG = True\n")
            self.assertEqual((out / "red/test/test_tool.py").read_text(encoding="utf-8"), "new\n")
            self.assertEqual((out / "green/tool.py").read_text(encoding="utf-8"), "BUG = False\n")
            # Both paths name a real file outside the merge tree, so only the
            # escape guard (not "not in the merge tree") can refuse them.
            for n, escape in enumerate(("../../clone/tool.py", "/etc/hostname")):
                with self.subTest(escape=escape):
                    if escape.startswith("/"):
                        self.assertTrue(Path(escape).is_file())
                    err = io.StringIO()
                    with contextlib.redirect_stderr(err):
                        again = mine_real_work.main(
                            ["prepare", "--clone", str(clone), "--base", base, "--merge", merge,
                             "--test-file", escape, "--out", str(tmp / f"esc{n}")])
                    self.assertEqual(again, 2)
                    self.assertIn("escapes the tree", err.getvalue())


if __name__ == "__main__":
    unittest.main()
