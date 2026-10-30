"""ADR 0012 part 1: historical, offline context resolution, without delivery.

All objects are committed in disposable repositories with no remotes. A
sentinel Claude exits 97 if metadata validation or resolution invokes it.
Run with the repository runner, or python3 -m unittest discover -s test/issues
-p test_issue_adr0012_context.py -v, inside a PID namespace.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness"))
GUIDANCE_REPO = "example-adopter/_agent-guidance"
PRIMARY = "example/registry"
CONSUMER = "example/consumer"
BUDGET = {"guidance_bytes": 65536, "skill_catalog_bytes": 65536,
          "skill_payload_bytes": 8388608}
BASE = b"# AGENTS.md\n\n## A rule\nHistorical guidance.\r\n"
STUB = b"# AGENTS.md\n\nRead the fleet-memory payload.\n"
SKILL = b"---\nname: sample\ndescription: Historical skill.\n---\n\nUse it.\r\n"


def digest(files):
    return hashlib.sha256(b"".join(
        name.encode() + b"\0" + hashlib.sha256(data).hexdigest().encode() + b"\n"
        for name, data in sorted(files.items()))).hexdigest()


def managed(base, sections=(), mode="stub", stub=STUB):
    head = ("<!-- BEGIN MANAGED SECTION — DO NOT EDIT ABOVE "
            '"## Repo-specific additions" -->\n'
            "<!-- Source: _agent-guidance -->\n"
            f"<!-- Sections: {' '.join(name for name, _ in sections) or 'none'} -->\n"
            f"<!-- Mode: {mode} -->\n\n").encode()
    return head + (stub if mode == "stub" else base) + b"".join(
        b"\n" + data for _, data in sections) + b"\n<!-- END MANAGED SECTION -->\n"


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="adr0012-context-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        sentinel = self.bin / "claude"
        sentinel.write_text("#!/bin/sh\nexit 97\n")
        sentinel.chmod(0o755)
        self.env = {"PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
                    "HOME": str(self.root), "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_CONFIG_GLOBAL": "/dev/null",
                    "GIT_AUTHOR_NAME": "Fixture", "GIT_COMMITTER_NAME": "Fixture",
                    "GIT_AUTHOR_EMAIL": "fixture@example.com",
                    "GIT_COMMITTER_EMAIL": "fixture@example.com",
                    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+00:00",
                    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+00:00",
                    "PYTHONDONTWRITEBYTECODE": "1", "CLAUDE_BIN": str(sentinel)}
        self.patch = mock.patch.dict(os.environ, self.env, clear=True)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.ctx = importlib.import_module("context")
        self.registry = self.repo("registry")
        self.guidance = self.repo("guidance")
        self.consumer = self.repo("consumer")
        self.source_sha = self.commit(self.registry, {
            "plugins/bundle/skills/sample/SKILL.md": SKILL,
            "plugins/bundle/skills/sample/data.bin": b"\x00\xff"})
        self.guidance_sha = self.commit(self.guidance, {
            "agents-md/base.md": BASE, "agents-md/stub.md": STUB,
            "agents-md/eval-coverage.yml": b"- id: rule\n  file: agents-md/base.md\n  heading: A rule\n",
            "repos.yml": b"default_sections: []\n",
            ".claude/hooks/fleet-memory.sh": b"#!/bin/sh\nexit 0\n"})
        self.git(self.guidance, "update-ref", "refs/remotes/origin/main", self.guidance_sha)
        self.lock = {"registry": PRIMARY, "ref": self.source_sha,
                     "bundles": ["bundle"], "skills": {
                         "bundle/sample": "sha256:" + digest({"SKILL.md": SKILL,
                                                                 "data.bin": b"\x00\xff"})},
                     "generated_from": self.source_sha}
        self.revision = self.commit(self.consumer, {
            "skills.lock": json.dumps(self.lock).encode(),
            "AGENTS.md": managed(BASE), ".claude/hooks/fleet-guidance.md": BASE})
        self.metadata = {"repository": CONSUMER, "revision": self.revision,
                         "guidance_revision": self.guidance_sha,
                         "budget": dict(BUDGET)}
        self.repositories = {CONSUMER: self.consumer, PRIMARY: self.registry,
                             GUIDANCE_REPO: self.guidance}

    def git(self, repo, *args):
        result = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C",
                                 str(repo), *args], env=self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                check=True)
        return result.stdout.decode().strip()

    def repo(self, name):
        path = self.root / name
        path.mkdir()
        self.git(path, "init", "-q")
        return path

    def commit(self, repo, files):
        for name, data in files.items():
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.git(repo, "add", "--all")
        self.git(repo, "commit", "-qm", "fixture", "--allow-empty")
        return self.git(repo, "rev-parse", "HEAD")

    def pin_guidance(self, files):
        """Commit guidance and publish it as origin/main, so the pin is eligible."""
        sha = self.commit(self.guidance, files)
        self.git(self.guidance, "update-ref", "refs/remotes/origin/main", sha)
        return sha

    def resolve(self, metadata=None, repositories=None):
        return self.ctx.resolve_context(metadata or self.metadata,
                                        repositories or self.repositories)

    def error(self, code, call):
        with self.assertRaises(self.ctx.ContextError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)
        self.assertIn(code, str(caught.exception))

    def update_lock(self, lock=None, raw=None):
        if lock is not None and isinstance(lock.get("ref"), str) and len(lock["ref"]) == 40:
            lock["generated_from"] = lock["ref"]
        self.metadata["revision"] = self.commit(self.consumer, {
            "skills.lock": raw if raw is not None else json.dumps(lock).encode()})

    def schema_error(self, edit):
        doc = {"context": copy.deepcopy(self.metadata)}
        edit(doc)
        self.error("invalid_context", lambda: self.ctx.validate_context(doc, "fixture.yaml"))

    def test_schema_accepts_legacy_absence_and_blocked_pin(self):
        self.ctx.validate_context({}, "fixture.yaml")
        self.metadata["guidance_revision"] = None
        self.ctx.validate_context({"context": self.metadata}, "fixture.yaml")
        self.error("guidance_unproven", self.resolve)

    def test_schema_refuses_unknown_keys(self):
        self.schema_error(lambda d: d["context"].update(extra="value"))

    def test_schema_refuses_missing_keys(self):
        self.schema_error(lambda d: d["context"].pop("revision"))

    def test_schema_refuses_non_mapping(self):
        self.schema_error(lambda d: d.update(context=[]))

    def test_schema_refuses_malformed_shas(self):
        for value in ("main", "f" * 39, "g" * 40, True, 5):
            with self.subTest(value=value):
                self.schema_error(lambda d: d["context"].update(revision=value))

    def test_schema_refuses_malformed_guidance_sha(self):
        self.schema_error(lambda d: d["context"].update(guidance_revision="main"))

    def test_schema_refuses_repository_controls_and_path_escape(self):
        for value in ("example/repo\n", "../repo", "repo", "example/repo\x7f"):
            with self.subTest(value=value):
                self.schema_error(lambda d: d["context"].update(repository=value))

    def test_schema_refuses_boolean_and_nonpositive_budgets(self):
        for value in (True, False, 0, -1, 2.5, "2", None):
            with self.subTest(value=value):
                self.schema_error(lambda d: d["context"]["budget"].update(guidance_bytes=value))

    def test_schema_refuses_unknown_and_missing_budget_keys(self):
        self.schema_error(lambda d: d["context"]["budget"].update(extra=1))
        self.schema_error(lambda d: d["context"]["budget"].pop("guidance_bytes"))

    def test_yaml_refuses_duplicate_context_key(self):
        raw = yaml.safe_dump({"context": self.metadata}) + "context: {}\n"
        self.error("duplicate_key", lambda: self.ctx.load_fixture_yaml(raw, "fixture.yaml"))

    def test_yaml_refuses_duplicate_nested_budget_key(self):
        raw = yaml.safe_dump({"context": self.metadata}).replace(
            "guidance_bytes: 65536", "guidance_bytes: 65536\n    guidance_bytes: 1")
        self.error("duplicate_key", lambda: self.ctx.load_fixture_yaml(raw, "fixture.yaml"))

    def test_yaml_refuses_syntax_and_root_type(self):
        for raw in ("context: [", "[]", ""):
            with self.subTest(raw=raw):
                self.error("invalid_fixture", lambda: self.ctx.load_fixture_yaml(raw, "fixture.yaml"))

    def test_context_checkout_flags_are_repeatable_and_explicit(self):
        parsed = self.ctx.parse_context_repos([
            f"{CONSUMER}={self.consumer}", f"{PRIMARY}={self.registry}"])
        self.assertEqual(parsed, {CONSUMER: self.consumer, PRIMARY: self.registry})

    def test_required_guidance_repository_identity_accepts_leading_underscore(self):
        self.metadata["repository"] = GUIDANCE_REPO
        self.ctx.validate_context({"context": self.metadata}, "fixture.yaml")
        self.assertEqual(self.ctx.parse_context_repos([f"{GUIDANCE_REPO}={self.guidance}"]),
                         {GUIDANCE_REPO: self.guidance})

    def test_context_checkout_flags_refuse_duplicates_and_controls(self):
        for values in (["x/y="], ["x/y=/tmp\n"], ["../y=/tmp"],
                       ["x/y=/tmp", "x/y=/tmp"], ["/tmp"]):
            with self.subTest(values=values):
                self.error("invalid_context_repo", lambda: self.ctx.parse_context_repos(values))

    def test_resolves_historical_objects_and_raw_bytes(self):
        self.commit(self.registry, {"plugins/bundle/skills/sample/SKILL.md": b"current"})
        self.commit(self.guidance, {"agents-md/base.md": b"current guidance"})
        (self.consumer / "skills.lock").write_text("uncommitted poison")
        frozen = self.resolve()
        self.assertEqual(frozen.guidance, BASE)
        self.assertEqual(frozen.skills[0].files["SKILL.md"], SKILL)
        self.assertEqual(frozen.skills[0].files["data.bin"], b"\x00\xff")
        self.assertEqual(frozen.manifest["sources"][0]["revision"], self.source_sha)
        self.assertEqual(frozen.manifest["lock"]["digest"],
                         hashlib.sha256(json.dumps(self.lock).encode()).hexdigest())
        self.assertEqual(self.git(self.registry, "rev-parse", "HEAD"),
                         self.git(self.registry, "rev-parse", "HEAD"))

    def test_manifest_and_payload_are_frozen_and_digest_is_deterministic(self):
        a, b = self.resolve(), self.resolve()
        self.assertEqual(a.digest, b.digest)
        self.assertEqual(len(a.digest), 64)
        with self.assertRaises(TypeError):
            a.manifest["measurements"]["guidance_bytes"] = 0
        with self.assertRaises(TypeError):
            a.skills[0].files["SKILL.md"] = b"tampered"

    def test_absent_lock_means_no_skills(self):
        self.git(self.consumer, "rm", "skills.lock")
        self.metadata["revision"] = self.commit(self.consumer, {})
        frozen = self.resolve(repositories={CONSUMER: self.consumer, GUIDANCE_REPO: self.guidance})
        self.assertEqual(frozen.skills, ())
        self.assertEqual(frozen.manifest["lock"]["state"], "absent")

    def test_federated_source_honors_its_ref_and_layout(self):
        extra = self.repo("federated")
        sha = self.commit(extra, {"skills/other/SKILL.md": SKILL})
        self.commit(extra, {"skills/other/SKILL.md": b"current"})
        lock = copy.deepcopy(self.lock)
        lock["sources"] = [{"registry": "example/federated", "ref": sha,
                            "bundles": ["extra"], "layout": "skills"}]
        lock["skills"]["extra/other"] = digest({"SKILL.md": SKILL})
        self.update_lock(lock)
        self.repositories["example/federated"] = extra
        frozen = self.resolve()
        self.assertEqual(len(frozen.skills), 2)
        self.assertEqual(frozen.manifest["sources"][1]["layout"], "skills")

    def test_source_tag_resolves_to_full_commit(self):
        self.git(self.registry, "tag", "-a", "v1", "-m", "fixture")
        lock = copy.deepcopy(self.lock)
        lock["ref"] = "v1"
        self.update_lock(lock)
        self.assertEqual(self.resolve().manifest["sources"][0]["revision"], self.source_sha)

    def test_primary_symbolic_ref_uses_generated_from_after_branch_moves(self):
        lock = copy.deepcopy(self.lock)
        lock["ref"] = "HEAD"
        self.update_lock(lock)
        self.commit(self.registry, {"plugins/bundle/skills/sample/SKILL.md": b"new"})
        self.assertEqual(self.resolve().manifest["sources"][0]["revision"], self.source_sha)

    def test_payload_preserves_executable_modes(self):
        path = self.registry / "plugins/bundle/skills/sample/run.sh"
        path.write_bytes(b"#!/bin/sh\nexit 0\n")
        path.chmod(0o755)
        sha = self.commit(self.registry, {})
        lock = copy.deepcopy(self.lock)
        lock["ref"] = lock["generated_from"] = sha
        lock["skills"]["bundle/sample"] = digest({"SKILL.md": SKILL, "data.bin": b"\x00\xff",
                                                   "run.sh": path.read_bytes()})
        self.update_lock(lock)
        self.assertEqual(self.resolve().skills[0].modes["run.sh"], "100755")

    def test_missing_context_repository_is_named(self):
        self.error("repository_unavailable", lambda: self.resolve(repositories={PRIMARY: self.registry}))

    def test_inaccessible_registry_is_named(self):
        mappings = dict(self.repositories)
        mappings.pop(PRIMARY)
        self.error("repository_unavailable", lambda: self.resolve(repositories=mappings))

    def test_missing_commit_is_named(self):
        self.metadata["revision"] = "a" * 40
        self.error("missing_object", self.resolve)

    def test_missing_registry_ref_is_named(self):
        lock = copy.deepcopy(self.lock)
        lock["ref"] = "a" * 40
        self.update_lock(lock)
        self.error("missing_object", self.resolve)

    def test_malformed_lock_and_duplicate_json_are_named(self):
        for raw in (b"[]", b"{", b'{"registry":"x/y","registry":"x/z"}'):
            self.update_lock(raw=raw)
            self.error("invalid_lock", self.resolve)

    def test_lock_refuses_unknown_keys_and_ambiguous_bundle_sources(self):
        for mutate in (lambda lock: lock.update(extra=True),
                       lambda lock: lock.update(sources=[{"registry": PRIMARY, "ref": self.source_sha,
                                                         "bundles": ["bundle"], "layout": "skills"}])):
            lock = copy.deepcopy(self.lock)
            mutate(lock)
            self.update_lock(lock)
            self.error("invalid_lock", self.resolve)

    def test_federated_source_refuses_ignored_generated_from(self):
        lock = copy.deepcopy(self.lock)
        lock["sources"] = [{"registry": "example/extra", "ref": self.source_sha,
                            "bundles": ["extra"], "layout": "skills",
                            "generated_from": self.source_sha}]
        self.update_lock(lock)
        self.error("invalid_lock", self.resolve)

    def test_lock_refuses_unknown_skill_bundle_and_controls(self):
        for mutate in (lambda lock: lock["skills"].update({"unknown/skill": "a" * 64}),
                       lambda lock: lock.update(ref="HEAD\n")):
            lock = copy.deepcopy(self.lock)
            mutate(lock)
            self.update_lock(lock)
            self.error("invalid_lock", self.resolve)

    def test_escaping_federated_layout_is_named(self):
        lock = copy.deepcopy(self.lock)
        lock["sources"] = [{"registry": "example/extra", "ref": self.source_sha,
                            "bundles": ["extra"], "layout": "../skills"}]
        self.update_lock(lock)
        self.error("invalid_layout", self.resolve)

    def test_digest_mismatch_is_named(self):
        lock = copy.deepcopy(self.lock)
        lock["skills"]["bundle/sample"] = "0" * 64
        self.update_lock(lock)
        self.error("digest_mismatch", self.resolve)

    def test_bundle_inventory_omission_is_named(self):
        sha = self.commit(self.registry, {"plugins/bundle/skills/omitted/SKILL.md": SKILL})
        lock = copy.deepcopy(self.lock)
        lock["ref"] = sha
        self.update_lock(lock)
        self.error("inventory_mismatch", self.resolve)

    def test_bundle_inventory_missing_skill_is_named(self):
        lock = copy.deepcopy(self.lock)
        lock["skills"]["bundle/missing"] = "a" * 64
        self.update_lock(lock)
        self.error("inventory_mismatch", self.resolve)

    def test_adopted_bundle_cannot_have_vacuous_inventory(self):
        lock = copy.deepcopy(self.lock)
        lock["bundles"] = ["absent"]
        lock["skills"] = {}
        self.update_lock(lock)
        self.error("inventory_mismatch", self.resolve)

    def test_symlink_and_gitlink_modes_are_named(self):
        symlink = self.registry / "plugins/bundle/skills/sample/link"
        symlink.symlink_to("../../../../outside")
        sha = self.commit(self.registry, {})
        lock = copy.deepcopy(self.lock)
        lock["ref"] = sha
        self.update_lock(lock)
        self.error("unsafe_git_mode", self.resolve)
        self.git(self.registry, "rm", "plugins/bundle/skills/sample/link")
        self.git(self.registry, "update-index", "--add", "--cacheinfo",
                 f"160000,{self.source_sha},plugins/bundle/skills/sample/submodule")
        self.git(self.registry, "commit", "-qm", "gitlink fixture")
        lock["ref"] = self.git(self.registry, "rev-parse", "HEAD")
        self.update_lock(lock)
        self.error("unsafe_git_mode", self.resolve)

    def test_git_paths_with_controls_are_named(self):
        sha = self.commit(self.registry, {"plugins/bundle/skills/sample/bad\nname": b"x"})
        lock = copy.deepcopy(self.lock)
        lock["ref"] = sha
        self.update_lock(lock)
        self.error("unsafe_path", self.resolve)

    def test_all_adopted_sections_are_read_and_verified(self):
        sections = (("python", b"## Python\nRule.\r\n"), ("docker", b"## Docker\nOther.\n"))
        files = {f"agents-md/sections/{name}.md": data for name, data in sections}
        files["agents-md/eval-coverage.yml"] = yaml.safe_dump([
            {"id": name, "file": f"agents-md/sections/{name}.md"} for name, _ in sections]).encode()
        self.metadata["guidance_revision"] = self.pin_guidance(files)
        self.metadata["revision"] = self.commit(self.consumer, {
            "AGENTS.md": managed(BASE, sections),
            ".agents-sync.yml": b"sections: [python, docker]\n"})
        frozen = self.resolve()
        self.assertEqual(frozen.guidance, BASE + b"".join(b"\n" + data for _, data in sections))
        self.assertEqual(frozen.manifest["guidance"]["sections"], ("python", "docker"))
        self.assertEqual(len(frozen.manifest["guidance"]["files"]), 3)

    def test_guidance_mismatch_is_unproven(self):
        self.metadata["guidance_revision"] = self.pin_guidance({"agents-md/base.md": b"new"})
        self.error("guidance_unproven", self.resolve)

    def test_optin_section_mismatch_with_identical_base_is_unproven(self):
        section = (("python", b"## Python\nShipped section.\n"),)
        self.metadata["guidance_revision"] = self.pin_guidance({
            "agents-md/sections/python.md": b"## Python\nDifferent section.\n",
            "agents-md/eval-coverage.yml": b"- id: python\n  file: agents-md/sections/python.md\n"})
        self.metadata["revision"] = self.commit(self.consumer, {
            "AGENTS.md": managed(BASE, section), ".agents-sync.yml": b"sections: [python]\n"})
        self.error("guidance_unproven", self.resolve)

    def test_managed_block_refuses_only_trailing_newline_or_whitespace_difference(self):
        for mode in ("stub", "full"):
            correct = managed(BASE, mode=mode)
            for suffix in (b"", b"\n\n", b" \n", b"\t\n"):
                with self.subTest(mode=mode, suffix=suffix):
                    changed = correct.replace(b"\n<!-- END MANAGED SECTION -->", suffix + b"<!-- END MANAGED SECTION -->")
                    self.metadata["revision"] = self.commit(self.consumer, {"AGENTS.md": changed})
                    self.error("guidance_unproven", self.resolve)
            self.metadata["revision"] = self.commit(self.consumer, {"AGENTS.md": correct})
            self.assertEqual(self.resolve().guidance, BASE)

    def test_payload_copy_refuses_only_trailing_newline_or_whitespace_difference(self):
        for copied in (BASE.rstrip(b"\r\n"), BASE + b"\n", BASE + b" ", BASE + b"\t"):
            with self.subTest(copied=copied):
                self.metadata["revision"] = self.commit(self.consumer, {".claude/hooks/fleet-guidance.md": copied})
                self.error("guidance_unproven", self.resolve)
        self.metadata["revision"] = self.commit(self.consumer, {".claude/hooks/fleet-guidance.md": BASE})
        self.assertEqual(self.resolve().guidance, BASE)

    def test_stub_alone_cannot_prove_full_guidance(self):
        self.git(self.consumer, "rm", ".claude/hooks/fleet-guidance.md")
        self.metadata["revision"] = self.commit(self.consumer, {})
        self.error("guidance_unproven", self.resolve)

    def test_full_managed_block_proves_guidance_without_payload_copy(self):
        self.git(self.consumer, "rm", ".claude/hooks/fleet-guidance.md")
        self.metadata["revision"] = self.commit(self.consumer, {"AGENTS.md": managed(BASE, mode="full")})
        self.assertEqual(self.resolve().guidance, BASE)

    def test_exact_payload_copy_without_agents_proves_zero_optins(self):
        self.git(self.consumer, "rm", "AGENTS.md")
        self.metadata["revision"] = self.commit(self.consumer, {})
        frozen = self.resolve()
        self.assertEqual(frozen.guidance, BASE)
        self.assertEqual(frozen.manifest["guidance"]["mode"], "copy")
        self.assertIsNone(frozen.manifest["guidance"]["managed_digest"])
        self.metadata["revision"] = self.commit(self.consumer, {".agents-sync.yml": b"sections: [python]\n"})
        self.error("guidance_unproven", self.resolve)

    def test_historical_full_base_allows_absent_manifest_and_hook_without_optins(self):
        self.git(self.guidance, "rm", "agents-md/eval-coverage.yml", ".claude/hooks/fleet-memory.sh")
        self.metadata["guidance_revision"] = self.pin_guidance({})
        self.git(self.consumer, "rm", ".claude/hooks/fleet-guidance.md")
        block = managed(BASE, mode="full").replace(b"<!-- Mode: full -->\n", b"")
        self.metadata["revision"] = self.commit(self.consumer, {"AGENTS.md": block})
        frozen = self.resolve()
        self.assertEqual(frozen.guidance, BASE)
        self.assertEqual(frozen.manifest["guidance"]["manifest_state"], "absent")
        self.assertEqual(frozen.manifest["guidance"]["hook_state"], "absent")

    def test_missing_managed_section_list_is_unproven(self):
        self.metadata["revision"] = self.commit(self.consumer, {"AGENTS.md": b"# Local guidance\n"})
        self.error("guidance_unproven", self.resolve)

    def test_contradictory_optin_configuration_is_unproven(self):
        self.metadata["revision"] = self.commit(self.consumer, {".agents-sync.yml": b"sections: [python]\n"})
        self.error("guidance_unproven", self.resolve)

    def test_false_zero_and_empty_string_optins_are_not_empty_lists(self):
        for value in (False, 0, ""):
            self.metadata["revision"] = self.commit(self.consumer, {
                ".agents-sync.yml": yaml.safe_dump({"sections": value}).encode()})
            with self.subTest(value=value):
                self.error("guidance_unproven", self.resolve)

    def test_missing_adopted_section_is_named(self):
        self.metadata["revision"] = self.commit(self.consumer, {
            "AGENTS.md": managed(BASE, (("missing", b"## Missing\n"),)),
            ".agents-sync.yml": b"sections: [missing]\n"})
        self.error("missing_section", self.resolve)

    def test_guidance_manifest_shape_is_named(self):
        self.metadata["guidance_revision"] = self.pin_guidance({"agents-md/eval-coverage.yml": b"{}"})
        self.error("invalid_guidance_manifest", self.resolve)

    def test_find_guidance_revision_uses_exact_bytes_not_latest(self):
        self.commit(self.guidance, {"agents-md/base.md": b"new"})
        pin = self.ctx.find_guidance_revision(CONSUMER, self.revision, self.repositories)
        self.assertEqual(pin.revision, self.guidance_sha)
        self.assertEqual(pin.matching_revisions, 1)

    def test_find_guidance_revision_counts_unchanged_ancestor_commits_and_breaks_ties(self):
        second = self.commit(self.guidance, {"unrelated.txt": b"unrelated"})
        third = self.commit(self.guidance, {"unrelated.txt": b"another"})
        self.git(self.guidance, "update-ref", "refs/remotes/origin/main", third)
        result = self.ctx.find_guidance_revision(CONSUMER, self.revision, self.repositories)
        self.assertEqual(result.matching_revisions, 3)
        self.assertEqual(result.revision, min(self.guidance_sha, second, third))

    def test_find_guidance_revision_excludes_branch_only_and_newer_matches(self):
        with mock.patch.dict(self.env, GIT_COMMITTER_DATE="2027-01-01T00:00:00+00:00"):
            newer = self.commit(self.guidance, {"unrelated.txt": b"new"})
        self.git(self.guidance, "update-ref", "refs/remotes/origin/main", newer)
        self.git(self.guidance, "checkout", "--detach", self.guidance_sha)
        branch = self.commit(self.guidance, {"branch.txt": b"branch only"})
        self.git(self.guidance, "branch", "branch-only", branch)
        result = self.ctx.find_guidance_revision(CONSUMER, self.revision, self.repositories)
        self.assertEqual(result.revision, self.guidance_sha)
        self.assertEqual(result.matching_revisions, 1)

    def test_resolve_applies_the_eligibility_rule_not_only_the_byte_check(self):
        # Identical bytes, but off the default branch or newer than the context
        # commit: the byte check alone would accept both pins.
        self.git(self.guidance, "checkout", "-q", "--detach", self.guidance_sha)
        branch = self.commit(self.guidance, {"branch.txt": b"branch only"})
        self.error("guidance_unproven", lambda: self.resolve(
            {**self.metadata, "guidance_revision": branch}))
        with mock.patch.dict(os.environ, GIT_COMMITTER_DATE="2027-01-01T00:00:00+00:00"):
            self.env["GIT_COMMITTER_DATE"] = "2027-01-01T00:00:00+00:00"
            newer = self.commit(self.guidance, {"unrelated.txt": b"new"})
        self.env["GIT_COMMITTER_DATE"] = "2026-01-01T00:00:00+00:00"
        self.git(self.guidance, "update-ref", "refs/remotes/origin/main", newer)
        self.error("guidance_unproven", lambda: self.resolve(
            {**self.metadata, "guidance_revision": newer}))
        self.assertEqual(self.resolve().manifest["guidance"]["revision"], self.guidance_sha)

    def test_resolve_without_a_default_branch_is_unproven(self):
        self.git(self.guidance, "update-ref", "-d", "refs/remotes/origin/main")
        self.error("guidance_unproven", self.resolve)

    def test_find_guidance_revision_missing_default_branch_is_unproven(self):
        self.git(self.guidance, "update-ref", "-d", "refs/remotes/origin/main")
        self.error("guidance_unproven", lambda: self.ctx.find_guidance_revision(CONSUMER, self.revision, self.repositories))

    def test_budget_hard_caps_accept_equal_and_refuse_one_byte_more(self):
        caps = {"guidance_bytes": 1024 * 1024, "skill_catalog_bytes": 1024 * 1024,
                "skill_payload_bytes": 64 * 1024 * 1024}
        for key, cap in caps.items():
            metadata = copy.deepcopy(self.metadata)
            metadata["budget"][key] = cap
            self.ctx.validate_context({"context": metadata}, "fixture.yaml")
            metadata["budget"][key] += 1
            self.error("invalid_context", lambda: self.ctx.validate_context({"context": metadata}, "fixture.yaml"))

    def test_duplicate_top_level_fixture_keys_are_refused(self):
        for raw in ("prompt: first\nprompt: second\n", "subject: any\nsubject: guidance\n"):
            self.error("duplicate_key", lambda: self.ctx.load_fixture_yaml(raw, "fixture.yaml"))

    def test_unrelated_malformed_fixture_uses_invalid_fixture(self):
        for raw in ("prompt: [broken", "[]", "null", "? [unhashable]\n: value\n"):
            self.error("invalid_fixture", lambda: self.ctx.load_fixture_yaml(raw, "fixture.yaml"))

    def test_unrelated_malformed_fixture_exits_two_with_invalid_fixture_before_claude(self):
        fixture = self.root / "malformed-fixture"
        fixture.mkdir()
        for raw in ("prompt: [broken", "[]", "? [unhashable]\n: value\n"):
            (fixture / "fixture.yaml").write_text(raw)
            result = subprocess.run([sys.executable, "harness/run_eval.py", str(fixture), "--arm", "both"],
                                    cwd=ROOT, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn(b"invalid_fixture", result.stdout + result.stderr)
            self.assertNotIn(b"invalid_context", result.stdout + result.stderr)

    def test_production_trusted_fetch_preserves_symbolic_registry_refs_without_credentials(self):
        self.git(self.guidance, "branch", "main", self.guidance_sha)
        self.git(self.registry, "branch", "main", self.source_sha)
        self.git(self.registry, "tag", "-a", "v1", "-m", "Annotated fixture", self.source_sha)
        original_run = subprocess.run
        fetch_calls = []

        def local_transport(argv, **kwargs):
            argv = list(argv)
            self.assertEqual(argv[0], "git")
            for index, value in enumerate(argv):
                if isinstance(value, str) and value.startswith("https://github.com/"):
                    identity = value[len("https://github.com/"):-len(".git")]
                    fetch_calls.append((list(argv), dict(kwargs["env"])))
                    argv[index] = str(self.repositories[identity])
            return original_run(["git", *argv[1:]], **kwargs)

        for ref in ("main", "v1", "HEAD"):
            with self.subTest(ref=ref):
                lock = copy.deepcopy(self.lock)
                lock.pop("generated_from")
                lock["ref"] = ref
                self.update_lock(lock)
                with mock.patch.object(_context_scaffold.subprocess, "run", side_effect=local_transport):
                    with _context_scaffold.trusted_context_repositories(self.metadata) as mappings:
                        resolved = self.resolve(repositories=mappings)
                        self.assertEqual(resolved.skills[0].revision, self.source_sha)
                        for path in mappings.values():
                            self.assertEqual(self.git(path, "remote"), "")
        self.assertTrue(fetch_calls)
        for argv, environment in fetch_calls:
            self.assertIn("credential.helper=", argv)
            self.assertEqual(environment["GIT_TERMINAL_PROMPT"], "0")
            self.assertNotEqual(environment["HOME"], str(self.root))
            self.assertNotIn("GH_TOKEN", environment)

    def test_find_guidance_revision_refuses_unmatched_bytes(self):
        self.metadata["revision"] = self.commit(self.consumer, {".claude/hooks/fleet-guidance.md": b"unmatched"})
        self.error("guidance_unproven", lambda: self.ctx.find_guidance_revision(
            CONSUMER, self.metadata["revision"], self.repositories))

    def test_budget_is_measured_and_enforced_for_each_dimension(self):
        frozen = self.resolve()
        values = frozen.manifest["measurements"]
        self.assertEqual(values["guidance_bytes"], len(BASE))
        self.assertEqual(values["skill_payload_bytes"], len(SKILL) + 2)
        for key, value in values.items():
            metadata = copy.deepcopy(self.metadata)
            metadata["budget"][key] = value
            self.resolve(metadata)
            metadata["budget"][key] = value - 1
            self.error("budget_exceeded", lambda: self.resolve(metadata))

    def test_fixture_load_runs_schema_validation(self):
        import run_eval
        fixture = self.root / "fixture"
        fixture.mkdir()
        doc = {"context": self.metadata}
        doc["context"]["budget"]["guidance_bytes"] = True
        (fixture / "fixture.yaml").write_text(yaml.safe_dump(doc))
        self.error("invalid_context", lambda: run_eval.load_fixture(fixture))

    def test_objective_only_validates_metadata_without_resolution_or_claude(self):
        fixture = self.root / "fixture"
        (fixture / "seed").mkdir(parents=True)
        (fixture / "seed" / "marker.txt").write_text("marker")
        doc = {"skill": "sample", "context": self.metadata,
               "checks": [{"id": "marker", "type": "file_count", "paths": ["marker.txt"], "min": 1}]}
        doc["context"]["guidance_revision"] = None
        (fixture / "fixture.yaml").write_text(yaml.safe_dump(doc))
        result = subprocess.run([sys.executable, "harness/run_eval.py", str(fixture),
                                 "--arm", "objective-only", "--results-dir", str(self.root / "results"),
                                 "--context-repo", f"{CONSUMER}={self.root / 'nonexistent'}"],
                                cwd=ROOT, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_invalid_metadata_exits_two_before_claude(self):
        fixture = self.root / "fixture"
        (fixture / "seed").mkdir(parents=True)
        doc = {"skill": "sample", "context": copy.deepcopy(self.metadata)}
        doc["context"]["revision"] = "main"
        (fixture / "fixture.yaml").write_text(yaml.safe_dump(doc))
        result = subprocess.run([sys.executable, "harness/run_eval.py", str(fixture),
                                 "--arm", "both"], cwd=ROOT, env=self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn(b"invalid_context", result.stdout + result.stderr)



sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "test" / "issues"))

# The scaffold keeps trusted candidate provenance before stripping the seed.
import types as _scaffold_types
import scaffold_real_work as _context_scaffold
from test_issue_real_work_scaffold import _BuildCase as _ContextBuildCase


class _BlockedScaffoldContext(_context_scaffold.guidance.GuidanceError):
    code = "guidance_unproven"


class ScaffoldContextTests(_ContextBuildCase):

    def setUp(self):
        super().setUp()
        sentinel = self.tmp / "bin" / "claude"
        sentinel.write_text("#!/bin/sh\nexit 97\n", encoding="utf-8")
        sentinel.chmod(0o755)

    def context_mock(self):
        mocked = mock.MagicMock()
        mocked.ContextError = _BlockedScaffoldContext
        mocked.BUDGET_KEYS = frozenset(BUDGET)
        mocked.BUDGET_MAX = {"guidance_bytes": 1024 * 1024,
                             "skill_catalog_bytes": 1024 * 1024,
                             "skill_payload_bytes": 64 * 1024 * 1024}
        mocked.find_guidance_revision.return_value = _scaffold_types.SimpleNamespace(
            revision="a" * 40, matching_revisions=3)
        mocked.resolve_context.return_value = _scaffold_types.SimpleNamespace(manifest={
            "measurements": {"guidance_bytes": 101, "skill_catalog_bytes": 4,
                             "skill_payload_bytes": 0}})
        return mocked

    def test_build_records_candidate_context_and_measured_limits_before_trimming(self):
        mocked = self.context_mock()
        events = []
        mocked.find_guidance_revision.side_effect = lambda *args: events.append("pin") or _scaffold_types.SimpleNamespace(
            revision="a" * 40, matching_revisions=3)
        mocked.resolve_context.side_effect = lambda *args: events.append("measure") or _scaffold_types.SimpleNamespace(
            manifest={"measurements": {"guidance_bytes": 101, "skill_catalog_bytes": 4,
                                       "skill_payload_bytes": 0}})
        original_trim = _context_scaffold.trim_seed
        def trim(*args):
            events.append("trim")
            return original_trim(*args)
        with mock.patch.object(_context_scaffold, "context", mocked, create=True), \
                mock.patch.object(_context_scaffold, "trim_seed", side_effect=trim):
            target = self.build(issue=None)
        fixture = yaml.safe_load((target / "fixture.yaml").read_bytes())
        self.assertEqual(events, ["pin", "measure", "trim"])
        self.assertEqual(fixture["context"], {
            "repository": "example/toy", "revision": self.base,
            "guidance_revision": "a" * 40,
            "budget": {"guidance_bytes": 127, "skill_catalog_bytes": 5,
                       "skill_payload_bytes": 1}})
        mocked.find_guidance_revision.assert_called_once_with(
            "example/toy", self.base, {"example/toy": self.clone})
        self.assertFalse(self.gh_log.exists())
        self.assertIn("3 eligible byte-matching revisions", (target / "fixture.yaml").read_text())

    def test_build_marks_unproven_guidance_blocked_without_guessing_a_pin(self):
        mocked = self.context_mock()
        mocked.find_guidance_revision.side_effect = _BlockedScaffoldContext("unproven")
        with mock.patch.object(_context_scaffold, "context", mocked, create=True):
            target = self.build(issue=None)
        fixture = yaml.safe_load((target / "fixture.yaml").read_bytes())
        self.assertEqual(fixture["context"]["repository"], "example/toy")
        self.assertEqual(fixture["context"]["revision"], self.base)
        self.assertIsNone(fixture["context"]["guidance_revision"])
        self.assertEqual(set(fixture["context"]["budget"].values()), {1})
        self.assertIn("BLOCKED: guidance_unproven", (target / "fixture.yaml").read_text())
        mocked.resolve_context.assert_not_called()

    def test_static_check_requires_structured_context(self):
        with mock.patch.object(_context_scaffold, "context", self.context_mock(), create=True):
            target = self.build(issue=None)
        path = target / "fixture.yaml"
        fixture = yaml.safe_load(path.read_bytes())
        fixture.pop("context", None)
        path.write_text(yaml.safe_dump(fixture), encoding="utf-8")
        problems, _ = _context_scaffold.static_problems(target)
        self.assertTrue(any("context" in problem for problem in problems), problems)

    def test_build_accepts_explicit_source_checkout_mappings(self):
        mocked = self.context_mock()
        spec_path = self.tmp / "spec.json"
        spec_path.write_text(json.dumps({"task_text": "Repair the calculation.",
            "checker": {"files": ["tests/test_tool.py"],
                        "argv": ["python3", "-m", "unittest"],
                        "fail_to_pass": ["tests.test_tool.ToolTests.test_add"]}}), encoding="utf-8")
        supplied = {"example/registry": self.tmp / "registry"}
        with mock.patch.object(_context_scaffold, "context", mocked, create=True):
            _context_scaffold.build(self.candidates, "example__toy__7", self.clone,
                                    spec_path, self.dest, repositories=supplied)
        self.assertEqual(mocked.find_guidance_revision.call_args.args[2],
                         {**supplied, "example/toy": self.clone})

    def test_build_measures_immutable_resolver_manifest(self):
        mocked = self.context_mock()
        mocked.resolve_context.return_value = _scaffold_types.SimpleNamespace(manifest=_scaffold_types.MappingProxyType({
            "measurements": _scaffold_types.MappingProxyType({"guidance_bytes": 101,
                "skill_catalog_bytes": 4, "skill_payload_bytes": 0})}))
        with mock.patch.object(_context_scaffold, "context", mocked, create=True):
            target = self.build(issue=None)
        fixture = yaml.safe_load((target / "fixture.yaml").read_bytes())
        self.assertEqual(fixture["context"]["budget"]["guidance_bytes"], 127)

    def test_build_refuses_headroom_that_exceeds_hard_budget_caps(self):
        mocked = self.context_mock()
        mocked.resolve_context.return_value.manifest["measurements"]["guidance_bytes"] = 1024 * 1024
        with mock.patch.object(_context_scaffold, "context", mocked, create=True):
            with self.assertRaisesRegex(_context_scaffold.ScaffoldError, "hard budget caps"):
                self.build(issue=None)

    def test_snapshot_gate_refuses_missing_context_before_any_candidate_read(self):
        with mock.patch.object(_context_scaffold, "context", self.context_mock(), create=True):
            target = self.build(issue=None)
        path = target / "fixture.yaml"
        text = path.read_text()
        fixture = yaml.safe_load(text)
        fixture.pop("context")
        comments = "\n".join(line for line in text.splitlines() if line.startswith("#"))
        path.write_text(comments + "\n\n" + yaml.safe_dump(fixture), encoding="utf-8")
        with mock.patch.object(_context_scaffold, "issue_snapshot") as read:
            with self.assertRaisesRegex(_context_scaffold.ingest.Rejected, "context"):
                _context_scaffold.gate_snapshot(target, "toy-7", self.fleet)
        read.assert_not_called()

    def test_gate_corroborates_the_trusted_commit_response_revision(self):
        self.set_gh(commit={"sha": "b" * 40, "parents": [{"sha": self.base}]})
        metadata = {"repository": "example/toy", "revision": self.base,
            "guidance_revision": None, "budget": {"guidance_bytes": 1,
            "skill_catalog_bytes": 1, "skill_payload_bytes": 1}}
        with self.assertRaisesRegex(_context_scaffold.ScaffoldError, "merge revision"):
            _context_scaffold.issue_snapshot("example/toy", 7, self.fleet, metadata)


class ScaffoldContextGateTests(unittest.TestCase):

    def metadata(self):
        return {"repository": "example/toy", "revision": "a" * 40,
                "guidance_revision": None,
                "budget": {"guidance_bytes": 1, "skill_catalog_bytes": 1,
                           "skill_payload_bytes": 1}}

    def test_gate_accepts_only_trusted_candidate_repository_and_base(self):
        _context_scaffold.gate_context(self.metadata(), "example/toy",
                                      {"base": {"sha": "a" * 40}}, {"parents": [{"sha": "c" * 40}]})

    def test_gate_refuses_structured_repository_spoof(self):
        metadata = self.metadata()
        metadata["repository"] = "example/other"
        with self.assertRaisesRegex(_context_scaffold.ingest.Rejected, "context repository"):
            _context_scaffold.gate_context(metadata, "example/toy", {"base": {"sha": "a" * 40}},
                                          {"parents": [{"sha": "c" * 40}]})

    def test_gate_refuses_a_forged_context_base(self):
        metadata = self.metadata()
        metadata["revision"] = "b" * 40
        with self.assertRaisesRegex(_context_scaffold.ingest.Rejected, "context revision"):
            _context_scaffold.gate_context(metadata, "example/toy", {"base": {"sha": "a" * 40}},
                                          {"parents": [{"sha": "c" * 40}]})

    def test_gate_refuses_missing_trusted_candidate_base(self):
        with self.assertRaisesRegex(_context_scaffold.ingest.Rejected, "base revision"):
            _context_scaffold.gate_context(self.metadata(), "example/toy", {"base": {"ref": "main"}},
                                          {"parents": [{"sha": "c" * 40}]})

    def test_gate_uses_merge_first_parent_as_the_miners_candidate_base(self):
        _context_scaffold.gate_context(self.metadata(), "example/toy",
                                      {"base": {"sha": "b" * 40}},
                                      {"parents": [{"sha": "a" * 40}, {"sha": "c" * 40}]})

    def test_gate_refuses_a_malformed_merge_parent(self):
        with self.assertRaisesRegex(_context_scaffold.ingest.Rejected, "base revision"):
            _context_scaffold.gate_context(self.metadata(), "example/toy", {"base": {"sha": "a" * 40}},
                                          {"parents": [{"sha": True}, {"sha": "c" * 40}]})

if __name__ == "__main__":
    unittest.main()
