"""Offline checks for daily coverage publication and public GAP proposals."""

from __future__ import annotations

import base64
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "test"))
from lockfile_pins import lock_problem  # noqa: E402
import scaffold_skill_gaps as gaps  # noqa: E402


def world(public_gaps=5):
    registries = []
    totals = {"total": 0, "covered": 0, "skipped": 0, "gap": 0}
    for name, policy in gaps.REGISTRY_POLICY.items():
        private = name in gaps.eval_coverage.PRIVATE_REGISTRIES
        rows = [] if private else [
            {"registry": name, "bundle": None, "skill": f"public-{index}",
             "status": "gap", "fixtures": 0, "reason": None}
            for index in range(public_gaps if name == "agentskills" else 1)]
        counts = {"total": len(rows) if not private else 1,
                  "covered": 0, "skipped": 0,
                  "gap": len(rows) if not private else 1}
        for key in totals:
            totals[key] += counts[key]
        reg = {"name": name, "layout": policy["layout"], "private": private,
               "counts": counts, "skills": rows, "names_withheld": private}
        if not private:
            reg["url"] = policy["url"]
        registries.append(reg)
    census = {"schema": 1, "include_private_names": False,
              "registries": registries, "totals": totals, "problems": []}
    sources = {"repository_revision": "a" * 40,
               "guidance_revision": "b" * 40,
               "registries": {name: "c" * 40 for name in gaps.REGISTRY_POLICY
                              if name not in gaps.eval_coverage.PRIVATE_REGISTRIES}}
    return census, sources


def canonical_pr(skill="public-0", *, number=121, state="open", labels=None):
    return {"number": number, "state": state,
            "body": gaps.marker("agentskills", skill),
            "html_url": f"https://github.com/{gaps.REPO}/pull/{number}",
            "head": {"ref": gaps.branch_name("agentskills", skill),
                     "repo": {"full_name": gaps.REPO}},
            "base": {"ref": "main", "repo": {"full_name": gaps.REPO}},
            "labels": [{"name": name} for name in
                       (labels if labels is not None else ["eval-scaffold"])]}


def canonical_issue(pr, skill="public-0", *, issue_id=123, state="open", labels=None):
    return {"id": issue_id, "number": issue_id, "state": state,
            "html_url": f"https://github.com/{gaps.REPO}/issues/{issue_id}",
            "title": f"Author eval for agentskills/{skill}",
            "user": {"login": "github-actions[bot]", "type": "Bot"},
            "body": (f"{gaps.marker('agentskills', skill)}\n"
                     f"Tracking parent: https://github.com/{gaps.REPO}/issues/62\n"
                     f"Draft PR: {pr['html_url']}\n"),
            "labels": [{"name": name} for name in (labels or [])]}


class FakeGitHub:
    def __init__(self, *, hold_parent=False, prs=None, issues=None, children=None):
        self.hold_parent = hold_parent
        self.prs = list(prs or [])
        self.issues = list(issues or [])
        self.children = list(children or [])
        self.calls = []
        self.number = 300

    def pages(self, path):
        self.calls.append(("PAGES", path, None))
        if "/pulls?" in path:
            rows = self.prs
        elif "/sub_issues" in path:
            rows = self.children
        else:
            rows = self.issues
        if "state=open" in path:
            return [item for item in rows if item.get("state", "open") == "open"]
        if "state=closed" in path:
            return [item for item in rows if item.get("state") == "closed"]
        return list(rows)

    def request(self, method, path, payload=None, *, missing_ok=False):
        self.calls.append((method, path, payload))
        if method == "GET":
            if path.endswith("/issues/62"):
                return {"state": "open", "labels":
                        [{"name": "on-hold"}] if self.hold_parent else []}
            if "/contents/" in path or "/git/ref/" in path:
                return None
            if "/git/commits/" in path:
                return {"tree": {"sha": "d" * 40}}
        if path.endswith("/git/blobs"):
            return {"sha": "e" * 40}
        if path.endswith("/git/trees"):
            return {"sha": "f" * 40}
        if path.endswith("/git/commits"):
            return {"sha": "1" * 40}
        if path.endswith("/pulls"):
            self.number += 1
            pr = {"number": self.number, "body": payload["body"], "state": "open",
                  "html_url": f"https://github.com/{gaps.REPO}/pull/{self.number}",
                  "head": {"ref": payload["head"], "repo": {"full_name": gaps.REPO}},
                  "base": {"ref": payload["base"], "repo": {"full_name": gaps.REPO}},
                  "labels": []}
            self.prs.append(pr)
            return pr
        if path.endswith("/issues"):
            self.number += 1
            issue = {"id": self.number, "number": self.number,
                     "html_url": f"https://github.com/{gaps.REPO}/issues/{self.number}",
                     "body": payload["body"], "title": payload["title"], "state": "open",
                     "user": {"login": "github-actions[bot]", "type": "Bot"},
                     "labels": []}
            self.issues.append(issue)
            return issue
        if method == "POST" and path.endswith("/labels"):
            number = int(path.split("/")[-2])
            pr = next(item for item in self.prs if item.get("number") == number)
            pr["labels"].extend({"name": name} for name in payload["labels"])
            return pr["labels"]
        if method == "POST" and path.endswith("/sub_issues"):
            self.children.append({"id": payload["sub_issue_id"]})
        return {}


class DraftTests(unittest.TestCase):
    def test_generated_fixture_is_a_context_valid_todo_not_a_claimed_eval(self):
        census, sources = world()
        reg, row = gaps.public_gaps(census)[0]
        files = gaps.draft_files(reg, row, sources)
        fixture = next(content for path, content in files.items()
                       if path.endswith("fixture.yaml"))
        parsed = gaps.context.load_fixture_yaml(fixture, "fixture.yaml")
        self.assertTrue(parsed["draft"])
        self.assertEqual(parsed["context"]["repository"], gaps.REPO)
        self.assertEqual(parsed["context"]["budget"],
                         {"guidance_bytes": 1, "skill_catalog_bytes": 1,
                          "skill_payload_bytes": 1})
        self.assertNotIn("objective_checks", parsed)
        self.assertIn("TODO", parsed["prompt"])
        self.assertTrue(fixture.startswith("# <!-- skill-gap:"))
        self.assertEqual(len(files), 2)

    def test_redacted_artifact_requires_complete_consistent_denominator(self):
        census, _ = world()
        self.assertEqual(len(gaps.public_gaps(census)), 7)
        private = next(reg for reg in census["registries"] if reg["private"])
        private["skills"] = [{"skill": "private-marker"}]
        with self.assertRaises(gaps.GapError):
            gaps.public_gaps(census)
        census, _ = world()
        census["registries"].pop()
        with self.assertRaises(gaps.GapError):
            gaps.public_gaps(census)
        census, _ = world()
        census["registries"][0] = copy.deepcopy(census["registries"][1])
        with self.assertRaises(gaps.GapError):
            gaps.public_gaps(census)

    def test_harness_legal_leading_punctuation_can_be_scaffolded(self):
        census, sources = world(public_gaps=3)
        rows = census["registries"][0]["skills"]
        for row, skill in zip(rows, ("_hidden", "-dash", ".dot")):
            row["skill"] = skill
        actual = gaps.public_gaps(census)
        self.assertEqual([row["skill"] for _, row in actual[:3]],
                         ["_hidden", "-dash", ".dot"])
        self.assertTrue(gaps.branch_name("agentskills", ".dot").endswith(
            "x" + ".dot".encode().hex()))
        self.assertNotEqual(gaps.branch_name("agentskills", "a..b"),
                            gaps.branch_name("agentskills", "x612e2e62"))
        self.assertTrue(gaps.branch_name("agentskills", "ends.lock").endswith(
            "x" + "ends.lock".encode().hex()))
        self.assertTrue(gaps.draft_files(*actual[0], sources))

    def test_census_artifact_has_public_urls_and_no_private_skill_rows(self):
        census, _ = world()
        # The capture wrapper enriches the existing census with source URLs.
        with tempfile.TemporaryDirectory() as tmp:
            resolved = {name: {**policy, "path": Path(tmp)}
                        for name, policy in gaps.REGISTRY_POLICY.items()}
            raw = copy.deepcopy(census)
            for reg in raw["registries"]:
                reg.pop("url", None)
            with (mock.patch.object(gaps.eval_coverage, "run",
                                    return_value=(0, json.dumps(raw))),
                  mock.patch.object(gaps.eval_coverage, "resolve", return_value=resolved),
                  mock.patch.object(gaps, "_revision", return_value="a" * 40)):
                gaps.census([], Path(tmp), Path(tmp) / "out")
            out = json.loads((Path(tmp) / "out" / "coverage.json").read_text())
            self.assertEqual(len(gaps.public_gaps(out)), 7)
            self.assertNotIn("private-marker", json.dumps(out))


class AutomationTests(unittest.TestCase):
    def test_dry_run_makes_no_github_calls_and_selects_at_most_three(self):
        census, sources = world()
        tags = gaps.automate(None, census, sources, "42", "a" * 40, write=False)
        self.assertEqual(len(tags), 3)

    def test_writes_only_coverage_paths_and_three_drafts_with_tracking(self):
        census, sources = world()
        api = FakeGitHub()
        tags = gaps.automate(api, census, sources, "42", "a" * 40, write=True,
                             as_of="2026-10-08T00:00:00Z")
        self.assertEqual(len(tags), 3)
        paths = [path for method, path, _ in api.calls if method == "PUT"]
        self.assertEqual(paths, [f"/repos/{gaps.REPO}/contents/coverage/42.json",
                                 f"/repos/{gaps.REPO}/contents/coverage/latest.json",
                                 f"/repos/{gaps.REPO}/contents/badges/coverage.json"])
        self.assertEqual(sum(path.endswith("/pulls") for method, path, _ in api.calls
                             if method == "POST"), 3)
        self.assertEqual(sum(path.endswith("/issues") for method, path, _ in api.calls
                             if method == "POST"), 3)
        self.assertEqual(sum(path.endswith("/sub_issues") for method, path, _ in api.calls
                             if method == "POST"), 3)
        self.assertEqual(sum(path.endswith("/labels") for method, path, _ in api.calls
                             if method == "POST"), 3)
        self.assertNotIn("private-marker", json.dumps(api.calls))
        latest = next(payload for method, path, payload in api.calls
                      if method == "PUT" and path.endswith("coverage/latest.json"))
        published = json.loads(base64.b64decode(latest["content"]))
        self.assertEqual(published["publication"]["as_of"], "2026-10-08T00:00:00Z")
        badge = next(payload for method, path, payload in api.calls
                     if method == "PUT" and path.endswith("badges/coverage.json"))
        self.assertIn("skipped", base64.b64decode(badge["content"]).decode())

    def test_held_parent_still_gets_census_but_no_proposals(self):
        census, sources = world()
        api = FakeGitHub(hold_parent=True)
        self.assertEqual(gaps.automate(api, census, sources, "42", "a" * 40,
                                       write=True, as_of="2026-10-08T00:00:00Z"), [])
        self.assertFalse(any(method == "POST" for method, _, _ in api.calls))
        self.assertEqual(sum(method == "PUT" for method, _, _ in api.calls), 3)

    def test_exact_marker_and_held_pr_prevent_duplicate_or_paused_work(self):
        census, sources = world()
        tag = gaps.marker("agentskills", "public-0")
        held_pr = canonical_pr(labels=["on-hold"])
        other_pr = canonical_pr("public-10", number=122)
        api = FakeGitHub(prs=[held_pr, other_pr])
        tags = gaps.automate(api, census, sources, "42", "a" * 40, write=True,
                             as_of="2026-10-08T00:00:00Z")
        self.assertNotIn(tag, tags)
        self.assertIn(gaps.marker("agentskills", "public-1"), tags)

    def test_completed_marker_pair_and_subissue_need_no_reproposal(self):
        census, sources = world(public_gaps=1)
        tag = gaps.marker("agentskills", "public-0")
        pr = canonical_pr()
        api = FakeGitHub(prs=[pr], issues=[canonical_issue(pr)],
                         children=[{"id": 123}])
        selected = gaps.automate(api, census, sources, "42", "a" * 40,
                                 write=True, as_of="2026-10-08T00:00:00Z")
        self.assertNotIn(tag, selected)

    def test_pr_only_resumes_and_unproven_issue_only_is_ignored(self):
        census, sources = world(public_gaps=2)
        pr_tag = gaps.marker("agentskills", "public-0")
        issue_tag = gaps.marker("agentskills", "public-1")
        api = FakeGitHub(prs=[canonical_pr()],
                         issues=[canonical_issue(canonical_pr("public-1"), "public-1")])
        tags = gaps.automate(api, census, sources, "42", "a" * 40,
                             write=True, as_of="2026-10-08T00:00:00Z")
        self.assertIn(pr_tag, tags)
        self.assertIn(issue_tag, tags)
        posts = [(path, payload) for method, path, payload in api.calls if method == "POST"]
        self.assertEqual(sum(path.endswith("/pulls") for path, _ in posts), 2)
        self.assertEqual(sum(path.endswith("/issues") for path, _ in posts), 3)
        self.assertFalse(any(pr_tag in (payload or {}).get("body", "")
                             for path, payload in posts if path.endswith("/pulls")))
        self.assertTrue(any(issue_tag in (payload or {}).get("body", "")
                            for path, payload in posts if path.endswith("/issues")))

    def test_held_tracking_issue_is_not_modified(self):
        census, sources = world(public_gaps=1)
        tag = gaps.marker("agentskills", "public-0")
        pr = canonical_pr()
        api = FakeGitHub(prs=[pr], issues=[canonical_issue(pr, labels=["on-hold"])])
        selected = gaps.automate(api, census, sources, "42", "a" * 40,
                                 write=True, as_of="2026-10-08T00:00:00Z")
        self.assertNotIn(tag, selected)

    def test_closed_marked_pr_or_issue_is_left_to_a_reviewer(self):
        census, sources = world(public_gaps=2)
        pr_tag = gaps.marker("agentskills", "public-0")
        issue_tag = gaps.marker("agentskills", "public-1")
        pr = canonical_pr("public-1", number=122)
        api = FakeGitHub(prs=[canonical_pr(state="closed"), pr],
                         issues=[canonical_issue(pr, "public-1", state="closed")])
        selected = gaps.automate(api, census, sources, "42", "a" * 40,
                                 write=True, as_of="2026-10-08T00:00:00Z")
        self.assertNotIn(pr_tag, selected)
        self.assertNotIn(issue_tag, selected)
        self.assertTrue(any("state=all" in path for method, path, _ in api.calls
                            if method == "PAGES"))

    def assert_unrelated_pr_is_ignored(self, change):
        census, sources = world(public_gaps=1)
        tag = gaps.marker("agentskills", "public-0")
        for state, labels in (("open", []), ("closed", []), ("open", ["on-hold"])):
            with self.subTest(state=state, labels=labels):
                pr = canonical_pr(state=state, labels=labels)
                change(pr)
                api = FakeGitHub(prs=[pr])
                selected = gaps.automate(api, census, sources, "42", "a" * 40,
                                        write=True, as_of="2026-10-08T00:00:00Z")
                self.assertIn(tag, selected)
                proposed = [payload for method, path, payload in api.calls
                            if method == "POST" and path.endswith("/pulls")]
                self.assertTrue(any(tag in payload["body"].splitlines()
                                    for payload in proposed))
                self.assertFalse(any(path.endswith("/issues/121/labels")
                                     for method, path, _ in api.calls if method == "POST"))

    def test_fork_head_repository_cannot_reserve_a_gap(self):
        self.assert_unrelated_pr_is_ignored(
            lambda pr: pr["head"]["repo"].update(full_name="example/probe"))

    def test_wrong_head_branch_cannot_reserve_a_gap(self):
        self.assert_unrelated_pr_is_ignored(
            lambda pr: pr["head"].update(ref="scaffold/unrelated"))

    def test_wrong_base_branch_cannot_reserve_a_gap(self):
        self.assert_unrelated_pr_is_ignored(lambda pr: pr["base"].update(ref="other"))

    def test_wrong_base_repository_cannot_reserve_a_gap(self):
        self.assert_unrelated_pr_is_ignored(
            lambda pr: pr["base"]["repo"].update(full_name="example/probe"))

    def test_pr_marker_must_be_an_exact_line(self):
        self.assert_unrelated_pr_is_ignored(lambda pr: pr.update(body="prefix " + pr["body"]))

    def test_pr_number_must_be_a_positive_integer(self):
        for value in (True, "121", 0, -1):
            with self.subTest(number=value):
                self.assert_unrelated_pr_is_ignored(lambda pr: pr.update(
                    number=value, html_url=f"https://github.com/{gaps.REPO}/pull/{value}"))

    def test_pr_url_must_identify_the_canonical_same_repository_pr(self):
        self.assert_unrelated_pr_is_ignored(
            lambda pr: pr.update(html_url="https://github.com/example/probe/pull/121"))

    def test_missing_pr_metadata_cannot_reserve_a_gap(self):
        for field in ("head", "base", "number", "html_url", "state"):
            with self.subTest(field=field):
                self.assert_unrelated_pr_is_ignored(lambda pr: pr.pop(field))

    def assert_unrelated_issue_is_ignored(self, change):
        census, sources = world(public_gaps=1)
        tag = gaps.marker("agentskills", "public-0")
        for state, labels in (("open", []), ("closed", []), ("open", ["on-hold"])):
            with self.subTest(state=state, labels=labels):
                pr = canonical_pr()
                issue = canonical_issue(pr, state=state, labels=labels)
                change(issue)
                api = FakeGitHub(prs=[pr], issues=[issue], children=[{"id": 123}])
                selected = gaps.automate(api, census, sources, "42", "a" * 40,
                                        write=True, as_of="2026-10-08T00:00:00Z")
                self.assertIn(tag, selected)
                proposed = [payload for method, path, payload in api.calls
                            if method == "POST" and path.endswith("/issues")]
                self.assertTrue(any(tag in payload["body"].splitlines()
                                    for payload in proposed))
                self.assertFalse(any(payload.get("sub_issue_id") == 123
                                     for method, path, payload in api.calls
                                     if method == "POST" and path.endswith("/sub_issues")))

    def test_tracking_issue_requires_the_workflow_bot_login(self):
        self.assert_unrelated_issue_is_ignored(
            lambda issue: issue["user"].update(login="example"))

    def test_tracking_issue_requires_bot_type(self):
        self.assert_unrelated_issue_is_ignored(lambda issue: issue["user"].update(type="User"))

    def test_tracking_issue_requires_the_expected_title(self):
        self.assert_unrelated_issue_is_ignored(lambda issue: issue.update(title="Unrelated"))

    def test_tracking_issue_requires_the_expected_parent(self):
        self.assert_unrelated_issue_is_ignored(
            lambda issue: issue.update(body=issue["body"].replace("issues/62", "issues/63")))

    def test_tracking_issue_requires_a_canonical_pr_link(self):
        self.assert_unrelated_issue_is_ignored(
            lambda issue: issue.update(body=issue["body"].replace("pull/121", "pull/122")))

    def test_tracking_issue_marker_must_be_an_exact_line(self):
        self.assert_unrelated_issue_is_ignored(
            lambda issue: issue.update(body="prefix " + issue["body"]))

    def test_tracking_issue_requires_complete_metadata(self):
        for field in ("body", "user", "title", "id", "state"):
            with self.subTest(field=field):
                self.assert_unrelated_issue_is_ignored(lambda issue: issue.pop(field))
        for value in (True, "123", 0, -1):
            with self.subTest(issue_id=value):
                self.assert_unrelated_issue_is_ignored(lambda issue: issue.update(id=value))

    def test_tracking_issue_without_a_canonical_pr_does_not_reserve_a_gap(self):
        census, sources = world(public_gaps=1)
        pr = canonical_pr()
        issue = canonical_issue(pr, state="closed", labels=["on-hold"])
        api = FakeGitHub(issues=[issue], children=[{"id": 123}])
        selected = gaps.automate(api, census, sources, "42", "a" * 40,
                                write=True, as_of="2026-10-08T00:00:00Z")
        self.assertIn(pr["body"], selected)

    def test_complete_canonical_pair_recovers_a_missing_scaffold_label(self):
        census, sources = world(public_gaps=1)
        pr = canonical_pr(labels=[])
        api = FakeGitHub(prs=[pr], issues=[canonical_issue(pr)], children=[{"id": 123}])
        selected = gaps.automate(api, census, sources, "42", "a" * 40,
                                write=True, as_of="2026-10-08T00:00:00Z")
        self.assertIn(pr["body"], selected)
        self.assertTrue(gaps.has_label(pr, "eval-scaffold"))
        self.assertEqual([(path, payload) for method, path, payload in api.calls
                          if method == "POST" and "/issues/121/" in path],
                         [(f"/repos/{gaps.REPO}/issues/121/labels",
                           {"labels": ["eval-scaffold"]})])

    def test_label_failure_after_creation_is_retried_without_a_duplicate_pr(self):
        census, sources = world(public_gaps=1)

        class FailFirstLabel(FakeGitHub):
            failed = False

            def request(self, method, path, payload=None, *, missing_ok=False):
                if method == "POST" and path.endswith("/labels") and not self.failed:
                    self.failed = True
                    self.calls.append((method, path, payload))
                    raise gaps.GapError("label write failed")
                return super().request(method, path, payload, missing_ok=missing_ok)

        api = FailFirstLabel()
        with self.assertRaises(gaps.GapError):
            gaps.automate(api, census, sources, "42", "a" * 40,
                          write=True, as_of="2026-10-08T00:00:00Z")
        created = api.prs[0]
        self.assertFalse(gaps.has_label(created, "eval-scaffold"))
        api.calls.clear()
        selected = gaps.automate(api, census, sources, "43", "a" * 40,
                                write=True, as_of="2026-10-08T00:00:00Z")
        self.assertIn(created["body"].splitlines()[0], selected)
        self.assertTrue(gaps.has_label(created, "eval-scaffold"))
        self.assertFalse(any(method == "POST" and path.endswith("/pulls")
                             and payload["head"] == created["head"]["ref"]
                             for method, path, payload in api.calls))
        self.assertTrue(any(method == "POST" and path.endswith(
            f"/issues/{created['number']}/labels") for method, path, _ in api.calls))

    def test_existing_scaffold_label_is_not_rewritten(self):
        census, sources = world(public_gaps=1)
        pr = canonical_pr()
        api = FakeGitHub(prs=[pr])
        selected = gaps.automate(api, census, sources, "42", "a" * 40,
                                write=True, as_of="2026-10-08T00:00:00Z")
        self.assertIn(pr["body"], selected)
        self.assertFalse(any(method == "POST" and path.endswith("/issues/121/labels")
                             for method, path, _ in api.calls))

    def test_canonical_holds_and_closures_prevent_label_reconciliation(self):
        census, sources = world(public_gaps=1)
        for subject in ("pr", "issue"):
            for state, labels in (("closed", []), ("open", ["on-hold"])):
                with self.subTest(subject=subject, state=state, labels=labels):
                    pr = canonical_pr(labels=[])
                    issue = canonical_issue(pr)
                    record = pr if subject == "pr" else issue
                    record.update(state=state, labels=[{"name": name} for name in labels])
                    api = FakeGitHub(prs=[pr], issues=[issue], children=[{"id": 123}])
                    selected = gaps.automate(api, census, sources, "42", "a" * 40,
                                            write=True, as_of="2026-10-08T00:00:00Z")
                    self.assertNotIn(pr["body"], selected)
                    self.assertFalse(any(method == "POST" and path.endswith("/issues/121/labels")
                                         for method, path, _ in api.calls))

    def test_unsafe_sources_or_census_make_zero_api_calls(self):
        census, sources = world()
        sources["registries"]["agentskills-private"] = "d" * 40
        api = FakeGitHub()
        with self.assertRaises(gaps.GapError):
            gaps.automate(api, census, sources, "42", "a" * 40, write=True)
        self.assertEqual(api.calls, [])
        census, sources = world()
        census["registries"][0]["counts"]["gap"] += 1
        with self.assertRaises(gaps.GapError):
            gaps.automate(api, census, sources, "42", "a" * 40, write=True)
        self.assertEqual(api.calls, [])

    def test_older_rerun_archives_but_does_not_replace_latest_or_badge(self):
        census, sources = world(public_gaps=1)
        old = {"publication": {"run_id": "43"}}

        class NewerLatest(FakeGitHub):
            def request(self, method, path, payload=None, *, missing_ok=False):
                if method == "GET" and path.endswith(
                        "/contents/coverage/latest.json?ref=persistent/eval-results"):
                    self.calls.append((method, path, payload))
                    encoded = base64.b64encode(json.dumps(old).encode()).decode()
                    return {"content": encoded[:20] + "\n" + encoded[20:],
                            "sha": "f" * 40}
                return super().request(method, path, payload, missing_ok=missing_ok)

        api = NewerLatest()
        self.assertEqual(gaps.automate(api, census, sources, "42", "a" * 40,
                                      write=True, as_of="2026-10-08T00:00:00Z"), [])
        writes = [path for method, path, _ in api.calls if method == "PUT"]
        self.assertEqual(writes, [f"/repos/{gaps.REPO}/contents/coverage/42.json"])
        self.assertFalse(any(method == "POST" for method, _, _ in api.calls))

    def test_existing_branch_requires_matching_draft_marker(self):
        census, sources = world()
        reg, row = gaps.public_gaps(census)[0]
        files = gaps.draft_files(reg, row, sources)
        branch = gaps.branch_name(reg["name"], row["skill"])

        class ExistingBranch(FakeGitHub):
            def request(self, method, path, payload=None, *, missing_ok=False):
                if "/git/ref/" in path:
                    self.calls.append((method, path, payload))
                    return {"object": {"sha": "1" * 40}}
                if path.endswith("/git/commits/" + "1" * 40):
                    self.calls.append((method, path, payload))
                    return {"parents": [{"sha": "a" * 40}]}
                if "/compare/" in path:
                    self.calls.append((method, path, payload))
                    return {"total_commits": 1, "files": [
                        {"filename": name, "status": "added"} for name in files]}
                if "/contents/" in path:
                    self.calls.append((method, path, payload))
                    fixture = next(content for name, content in files.items()
                                   if name.endswith("fixture.yaml"))
                    encoded = base64.b64encode(fixture.encode()).decode()
                    return {"content": encoded[:40] + "\n" + encoded[40:]}
                return super().request(method, path, payload, missing_ok=missing_ok)

        api = ExistingBranch()
        gaps._ensure_branch(api, branch, files, "a" * 40)
        self.assertFalse(any(method == "POST" for method, _, _ in api.calls))
        bad = {**files}
        fixture_path = next(path for path in bad if path.endswith("fixture.yaml"))
        bad[fixture_path] = bad[fixture_path].replace("skill-gap:", "wrong-gap:")
        with self.assertRaises(gaps.GapError):
            gaps._ensure_branch(api, branch, bad, "a" * 40)

        class ExtraChange(ExistingBranch):
            def request(self, method, path, payload=None, *, missing_ok=False):
                if "/compare/" in path:
                    return {"total_commits": 1, "files": [
                        {"filename": name, "status": "added"} for name in files] +
                        [{"filename": ".github/workflows/unrelated.yml", "status": "added"}]}
                return super().request(method, path, payload, missing_ok=missing_ok)

        with self.assertRaises(gaps.GapError):
            gaps._ensure_branch(ExtraChange(), branch, files, "a" * 40)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workflow = yaml.load(
            (ROOT / ".github/workflows/skill-coverage.yml").read_text(),
            Loader=yaml.BaseLoader)

    def test_schedule_dispatch_and_main_guard_with_dry_dispatch(self):
        triggers = self.workflow["on"]
        self.assertEqual(set(triggers), {"schedule", "workflow_dispatch"})
        self.assertEqual(triggers["workflow_dispatch"]["inputs"]["dry_run"]["default"],
                         "true")
        self.assertIn("refs/heads/main", self.workflow["jobs"]["census"]["if"])
        condition = self.workflow["jobs"]["publish"]["if"]
        for token in ("refs/heads/main", "schedule", "workflow_dispatch",
                      "inputs.dry_run == false"):
            self.assertIn(token, condition)
        summary = next(step for step in self.workflow["jobs"]["census"]["steps"]
                       if step["name"] == "Summarize the redacted census")
        self.assertIn("$GITHUB_STEP_SUMMARY", summary["run"])
        self.assertEqual(self.workflow["concurrency"],
                         {"group": "skill-coverage-main", "cancel-in-progress": "false"})

    def test_reader_and_writer_permissions_checkout_and_pins(self):
        self.assertEqual(self.workflow["permissions"], {})
        self.assertEqual(self.workflow["jobs"]["census"]["permissions"],
                         {"contents": "read"})
        writer = self.workflow["jobs"]["publish"]
        self.assertEqual(writer["permissions"],
                         {"contents": "write", "pull-requests": "write",
                          "issues": "write"})
        census_steps = self.workflow["jobs"]["census"]["steps"]
        checkouts = [step for step in census_steps if step.get("uses", "").startswith(
            "actions/checkout@")]
        self.assertEqual(len(checkouts), 6)
        self.assertTrue(all(step["with"]["persist-credentials"] == "false"
                            for step in checkouts))
        names = [step.get("name") for step in census_steps]
        mint = next(step for step in census_steps if step.get("id") == "registry-token")
        private = next(step for step in checkouts
                       if step["with"].get("repository", "").endswith("-private"))
        self.assertLess(census_steps.index(mint), census_steps.index(private))
        self.assertEqual(mint["uses"],
                         "actions/create-github-app-token@v3.2.0")
        self.assertEqual(mint["with"], {
            "client-id": "${{ vars.REGISTRY_READ_APP_CLIENT_ID }}",
            "private-key": "${{ secrets.REGISTRY_READ_APP_PEM }}",
            "owner": "example-adopter",
            "repositories": "agentskills-private",
            "permission-contents": "read"})
        self.assertEqual(private["with"]["token"],
                         "${{ steps.registry-token.outputs.token }}")
        self.assertTrue(all("token" not in step["with"] for step in checkouts
                            if step is not private))
        require = next(step for step in census_steps
                       if step.get("name") == "Require the private registry read credential")
        self.assertLess(names.index(require["name"]), census_steps.index(mint))
        self.assertEqual(require["env"], {
            "APP_CLIENT_ID": "${{ vars.REGISTRY_READ_APP_CLIENT_ID }}",
            "APP_PEM": "${{ secrets.REGISTRY_READ_APP_PEM }}"})
        self.assertIn("exit 1", require["run"])
        self.assertNotIn("${{", require["run"])
        self.assertNotIn("SKILL_COVERAGE_READ_TOKEN", json.dumps(self.workflow))
        for job in self.workflow["jobs"].values():
            for step in job["steps"]:
                if "uses" in step:
                    self.assertIsNone(lock_problem(step["uses"], "skill-coverage.yml"))
                if "run" in step:
                    self.assertNotIn("${{ github.event.", step["run"])
                    self.assertNotIn("${{ inputs.", step["run"])

    def test_ci_required_check_runs_on_new_workflow_changes(self):
        ci = yaml.load((ROOT / ".github/workflows/ci.yml").read_text(),
                       Loader=yaml.BaseLoader)
        path = ".github/workflows/skill-coverage.yml"
        self.assertIn(path, ci["on"]["push"]["paths"])
        salient = next(step for step in ci["jobs"]["suite"]["steps"]
                       if step.get("id") == "salient")
        self.assertIn(path, salient["env"]["SALIENT_PATHS"].splitlines())


if __name__ == "__main__":
    unittest.main()
