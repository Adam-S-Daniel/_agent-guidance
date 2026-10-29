"""ADR 0012 part 2: deliver the frozen context to both arms, minus the subject.

Every repository is a disposable Git repository with no remote. The `claude`
on PATH is a sentinel that exits 97, so nothing reaches a real CLI; agent
calls go to a recorder named by CLAUDE_BIN, which writes what each call saw
(argv, environment, the delivered profile) and answers like the CLI. Run with
the repository runner, inside a PID namespace.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from dataclasses import replace

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "scripts"))

import context  # noqa: E402
import delivery  # noqa: E402
import ingest_routine_results as ingest  # noqa: E402
import run_eval  # noqa: E402

GUIDANCE_REPO = "example-adopter/_agent-guidance"
PRIMARY = "example/registry"
CONSUMER = "example/consumer"
REGISTRY_URL = "https://github.com/example-adopter/acsi-agentskills"
BUDGET = {"guidance_bytes": 65536, "skill_catalog_bytes": 65536,
          "skill_payload_bytes": 8388608}
# Non-ASCII before and inside the sections, a fenced `##` that is not a
# heading, and a CRLF line: extents are measured on the raw bytes.
BASE = ("# AGENTS.md\n\nIntro — café ✓.\n\n"
        "## Alpha rule\nAlpha body é.\n\n```md\n## Not a heading\n```\n\n"
        "## Bravo rule\nBravo ü body.\r\n").encode("utf-8")
STUB = b"# AGENTS.md\n\nRead the fleet-memory payload.\n"
PYTHON = "## Python\nUse uv — ✓.\n".encode("utf-8")
DOCKER = "## Docker\nPin images ✓.\n".encode("utf-8")
MANIFEST = [
    {"id": "alpha", "file": "agents-md/base.md", "heading": "Alpha rule"},
    {"id": "bravo", "file": "agents-md/base.md", "heading": "Bravo rule"},
    {"id": "fenced", "file": "agents-md/base.md", "heading": "Not a heading"},
    {"id": "python", "file": "agents-md/sections/python.md", "heading": "Python"},
    {"id": "docker", "file": "agents-md/sections/docker.md", "heading": "Docker"},
]
# A deterministic stand-in for fleet-memory.sh: the same marked block, its
# version and its delivery stamp, which comes from the payload's mtime.
HOOK = b"""#!/bin/bash
set -u
P="$FLEET_GUIDANCE_PAYLOAD"; D="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
mkdir -p "$D"
v=$(sha256sum "$P" | cut -c1-8); s=$(stat -c %Y "$P")
{ printf '%s\\n' '<!-- BEGIN FLEET GUIDANCE (managed by _agent-guidance) \xe2\x80\x94 DO NOT EDIT -->'
  printf '<!-- fleet-guidance-version: %s -->\\n' "$v"
  printf '<!-- fleet-guidance-delivered: %s -->\\n' "$s"
  cat "$P"; printf '%s\\n' '<!-- END FLEET GUIDANCE -->'; } > "$D/CLAUDE.md.tmp" && mv "$D/CLAUDE.md.tmp" "$D/CLAUDE.md"
echo "fleet-guidance: installed (v$v)"
"""
SAMPLE = {"SKILL.md": b"---\nname: sample\ndescription: Sample skill.\n---\n\nUse it.\r\n",
          "scripts/run.sh": b"#!/bin/sh\necho run\n", "data.bin": b"\x00\xff"}
OTHER = {"SKILL.md": b"---\nname: other\ndescription: Other skill.\n---\n\nOther.\n"}
CANDIDATE = {"SKILL.md": SAMPLE["SKILL.md"] + b"Candidate body.\n"}
FRESH = {"SKILL.md": b"---\nname: fresh\ndescription: Not adopted.\n---\n\nFresh.\n"}

RECORDER = f"""#!{sys.executable}
import fnmatch, hashlib, json, os, sys
from pathlib import Path
argv = sys.argv[1:]
if argv == ["--version"]:
    with open(os.environ["VERSION_LOG"], "a") as fh:
        fh.write("version\\n")
    print("2.1.293 (Claude Code)")
    sys.exit(0)
cfg = os.environ.get("CLAUDE_CONFIG_DIR")
rec = {{"argv": argv, "cwd": os.getcwd(), "env": {{k: os.environ.get(k) for k in (
    "HOME", "TMPDIR", "CLAUDE_CONFIG_DIR", "CLAUDE_CODE_ENABLE_CFC",
    "CLAUDE_CODE_REMOTE_SESSION_ID", "CLAUDE_CODE_DISABLE_AUTO_MEMORY")}}}}
if cfg:
    p = Path(cfg)
    rec["claude_md"] = (p / "CLAUDE.md").read_bytes().hex() if (p / "CLAUDE.md").is_file() else None
    root = p / "plugins" / "marketplaces" / "skills-evals-context" / "plugins"
    rec["plugins"] = {{f.relative_to(root).as_posix(): hashlib.sha256(f.read_bytes()).hexdigest()
                      for f in sorted(root.rglob("*")) if f.is_file()}} if root.is_dir() else {{}}
    rec["settings"] = json.loads((p / "settings.json").read_text()) if (p / "settings.json").is_file() else None
    rec["registrations"] = {{name: (p / "plugins" / name).read_bytes().hex()
                             for name in ("known_marketplaces.json", "installed_plugins.json")}}
skills = Path(os.getcwd()) / ".claude" / "skills"
rec["workspace_skills"] = sorted(x.name for x in skills.iterdir()) if skills.is_dir() else []
if cfg and os.environ.get("REC_MODE") == "read_resources":
    policy = json.loads(argv[argv.index("--settings") + 1])
    filesystem = policy["sandbox"]["filesystem"]
    patterns = [r[len("Read(/"):-1].removesuffix("/**")
                for r in policy["permissions"]["deny"] if r.startswith("Read(/")]
    def within(path, root):
        return path == root or root in path.parents
    def denied_tool(path):
        return any(len(pattern.split("/")) == len(candidate.parts) and all(
            fnmatch.fnmatchcase(part.lower(), glob.lower())
            for part, glob in zip(candidate.parts[1:], pattern.split("/")[1:]))
            for pattern in patterns for candidate in (path, *path.parents))
    rec["resource_reads"] = {{}}
    probes = [root / "bundle/skills/sample/scripts/run.sh", p / "settings.json",
              p / "CLAUDE.md", p / "plugins/private/future", p / "projects/other/session.jsonl",
              Path(os.environ["HOME"]) / "private"]
    for probe in probes:
        probe = probe.resolve()
        shell_denied = any(within(probe, Path(r)) for r in filesystem["denyRead"])
        if any(within(probe, Path(r)) for r in filesystem.get("allowRead", [])):
            shell_denied = False
        allowed = not shell_denied and not denied_tool(probe)
        rec["resource_reads"][str(probe)] = {{"allowed": allowed,
            "bytes": probe.read_bytes().hex() if allowed else None}}
with open(os.environ["REC_LOG"], "a") as fh:
    fh.write(json.dumps(rec) + "\\n")
mode = os.environ.get("REC_MODE")
count = sum(json.loads(line)["env"]["CLAUDE_CONFIG_DIR"] == cfg
            for line in Path(os.environ["REC_LOG"]).read_text().splitlines())
if cfg and (mode == "write_profile" or (mode == "write_profile_second" and count == 2)):
    Path(cfg, "settings.json").write_text("{{}}")
print(json.dumps({{"type": "result", "is_error": False, "result": "done",
                  "total_cost_usd": 0, "usage": {{}}, "num_turns": 1,
                  "duration_ms": 1, "session_id": "s1", "modelUsage": {{}}}}))
"""


def digest(files):
    return context._file_digest(files)


def managed(base, sections=(), mode="full"):
    head = ("<!-- BEGIN MANAGED SECTION — DO NOT EDIT ABOVE "
            '"## Repo-specific additions" -->\n'
            "<!-- Source: _agent-guidance -->\n"
            f"<!-- Sections: {' '.join(name for name, _ in sections) or 'none'} -->\n"
            f"<!-- Mode: {mode} -->\n\n").encode()
    return head + base + b"".join(b"\n" + data for _, data in sections) \
        + b"\n<!-- END MANAGED SECTION -->\n"


class Base(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="adr0012-delivery-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        sentinel = self.bin / "claude"
        sentinel.write_text("#!/bin/sh\nexit 97\n")
        sentinel.chmod(0o755)
        self.recorder = self.root / "recorder"
        self.recorder.write_text(RECORDER, encoding="utf-8")
        self.recorder.chmod(0o755)
        self.log = self.root / "calls.jsonl"
        self.home = self.root / "home"
        self.home.mkdir()
        (self.home / ".claude").mkdir()
        (self.home / ".claude/settings.json").write_bytes(b'{"fixture": true}')
        (self.home / ".claude/.credentials.json").write_bytes(b'{"synthetic": true}')
        self.source_profile = tree(self.home / ".claude")
        self.version_log = self.root / "versions.log"
        self.env = {"PATH": str(self.bin) + os.pathsep + os.defpath,
                    "HOME": str(self.home), "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_CONFIG_GLOBAL": "/dev/null",
                    "GIT_AUTHOR_NAME": "Fixture", "GIT_COMMITTER_NAME": "Fixture",
                    "GIT_AUTHOR_EMAIL": "fixture@example.com",
                    "GIT_COMMITTER_EMAIL": "fixture@example.com",
                    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+00:00",
                    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+00:00",
                    "XDG_STATE_HOME": str(self.root / "state"),
                    "PYTHONDONTWRITEBYTECODE": "1", "CLAUDE_BIN": str(self.recorder),
                    "VERSION_LOG": str(self.version_log),
                    "CLAUDE_CODE_ENTRYPOINT": "cli"}
        patcher = mock.patch.dict(os.environ, self.env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.registry = self.repo("registry")
        self.guidance = self.repo("guidance")
        self.consumer = self.repo("consumer")
        files = {f"plugins/bundle/skills/sample/{k}": v for k, v in SAMPLE.items()}
        files.update({f"plugins/bundle/skills/other/{k}": v for k, v in OTHER.items()})
        files.update({f"plugins/spare/skills/fresh/{k}": v for k, v in FRESH.items()})
        self.source_sha = self.commit(self.registry, files)
        os.chmod(self.registry / "plugins/bundle/skills/sample/scripts/run.sh", 0o755)
        self.source_sha = self.commit(self.registry, {})
        self.guidance_sha = self.commit(self.guidance, {
            "agents-md/base.md": BASE, "agents-md/stub.md": STUB,
            "agents-md/sections/python.md": PYTHON, "agents-md/sections/docker.md": DOCKER,
            "agents-md/eval-coverage.yml": yaml.safe_dump(MANIFEST).encode(),
            "repos.yml": b"default_sections: []\n", ".claude/hooks/fleet-memory.sh": HOOK})
        self.git(self.guidance, "update-ref", "refs/remotes/origin/main", self.guidance_sha)
        self.lock = {"registry": PRIMARY, "ref": self.source_sha, "bundles": ["bundle"],
                     "skills": {"bundle/sample": "sha256:" + digest(SAMPLE),
                                "bundle/other": "sha256:" + digest(OTHER)},
                     "generated_from": self.source_sha}
        self.revision = self.commit(self.consumer, {
            "skills.lock": json.dumps(self.lock).encode(),
            "AGENTS.md": managed(BASE, (("python", PYTHON),)),
            ".agents-sync.yml": b"sections: [python]\n",
            ".claude/hooks/fleet-guidance.md": BASE})
        self.metadata = {"repository": CONSUMER, "revision": self.revision,
                         "guidance_revision": self.guidance_sha, "budget": dict(BUDGET)}
        self.repositories = {CONSUMER: self.consumer, PRIMARY: self.registry,
                             GUIDANCE_REPO: self.guidance}
        self.registries = {"acsi-agentskills": {
            "name": "acsi-agentskills", "path": self.registry,
            "layout": "plugins/*/skills/*/SKILL.md", "url": REGISTRY_URL}}

    def git(self, repo, *args):
        return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-C", str(repo), *args],
                              env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              check=True).stdout.decode().strip()

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

    def frozen(self):
        return context.resolve_context(self.metadata, self.repositories)

    def refused(self, code, call):
        with self.assertRaises(guidance_error()) as caught:
            call()
        self.assertEqual(caught.exception.code, code, str(caught.exception))

    def args(self, **extra):
        values = {"results_dir": self.root / "results", "read_deny": [], "timeout": None,
                  "no_judge": True, "registry": None, "guidance": None, "model": None,
                  "permission_mode": "auto", "effort": None, "harness_version": None,
                  "context_repos": dict(self.repositories), "skill_bundle": None}
        values.update(extra)
        return argparse.Namespace(**values)

    def plan(self, skill="sample", section=None, **extra):
        fixture = {"context": self.metadata}
        if section is None:
            fixture["skill"] = skill
            return run_eval._in_place_plan(fixture, self.args(**extra), self.registries, "skill")
        fixture["section"] = section
        return run_eval._in_place_plan(fixture, self.args(**extra), {}, "guidance")

    def seed(self, files=None):
        seed = self.root / "seed"
        for name, data in (files or {"README.md": b"x\n"}).items():
            (seed / name).parent.mkdir(parents=True, exist_ok=True)
            (seed / name).write_bytes(data)
        return seed

    def prepare(self, role, plan, seed=None, fixture=None):
        scratch = Path(tempfile.mkdtemp(prefix=f"{run_eval.ARM_WORKSPACE_PREFIX}test-"))
        self.addCleanup(shutil.rmtree, scratch, ignore_errors=True)
        workspace, env, delivered = run_eval._prepare_in_place(
            role, fixture or {}, seed or self.seed(), plan, scratch)
        self.addCleanup(run_eval.workspace_git.release, workspace)
        return scratch, workspace, env, delivered


def guidance_error():
    import guidance
    return guidance.GuidanceError


def tree(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mode & 0o111 != 0)
            for p in sorted(root.rglob("*")) if p.is_file()}


class SubjectTests(Base):
    def test_an_adopted_skill_is_removed_and_carries_its_deployed_digest(self):
        subject = delivery.resolve_skill_subject(self.frozen(), "sample", self.registries)
        self.assertEqual((subject.registry, subject.bundle, subject.skill, subject.action),
                         (PRIMARY, "bundle", "sample", "removed"))
        self.assertEqual(subject.deployed_digest, digest(SAMPLE))
        self.assertEqual(subject.tested_digest, subject.deployed_digest)
        self.assertEqual(subject.record()["bytes"], sum(map(len, SAMPLE.values())))

    def test_a_qualified_name_resolves_and_a_wrong_bundle_does_not(self):
        subject = delivery.resolve_skill_subject(self.frozen(), "bundle:sample", self.registries)
        self.assertEqual(subject.action, "removed")
        self.refused("skill_not_found", lambda: delivery.resolve_skill_subject(
            self.frozen(), "nobundle:sample", self.registries))

    def test_a_name_adopted_from_two_bundles_must_be_qualified(self):
        extra = {f"plugins/extra/skills/sample/{k}": v for k, v in OTHER.items()}
        sha = self.commit(self.registry, extra)
        lock = dict(self.lock, ref=sha, generated_from=sha, bundles=["bundle", "extra"])
        lock["skills"] = dict(self.lock["skills"], **{"extra/sample": "sha256:" + digest(OTHER)})
        self.metadata["revision"] = self.commit(self.consumer, {"skills.lock": json.dumps(lock).encode()})
        frozen = self.frozen()
        self.refused("ambiguous_skill", lambda: delivery.resolve_skill_subject(
            frozen, "sample", self.registries))
        subject = delivery.resolve_skill_subject(frozen, "extra:sample", self.registries)
        self.assertEqual((subject.bundle, subject.deployed_digest), ("extra", digest(OTHER)))

    def test_an_absent_skill_is_added_from_its_one_registry_copy(self):
        subject = delivery.resolve_skill_subject(self.frozen(), "fresh", self.registries)
        self.assertEqual((subject.registry, subject.bundle, subject.action),
                         ("example-adopter/acsi-agentskills", "spare", "added"))
        self.assertIsNone(subject.deployed_digest)
        self.assertEqual(subject.tested_digest, digest(FRESH))

    def test_duplicate_registry_copies_are_refused_not_sorted(self):
        for bundle in ("aaa", "zzz"):
            path = self.registry / "plugins" / bundle / "skills" / "twin"
            path.mkdir(parents=True)
            (path / "SKILL.md").write_bytes(f"---\nname: twin\ndescription: {bundle}.\n---\n".encode())
        self.refused("ambiguous_skill", lambda: delivery.resolve_skill_subject(
            self.frozen(), "twin", self.registries))
        self.assertEqual(delivery.resolve_skill_subject(
            self.frozen(), "zzz:twin", self.registries).bundle, "zzz")

    def test_registry_qualification_selects_only_named_source(self):
        subject = delivery.resolve_skill_subject(self.frozen(), "bundle:sample", self.registries,
                                                 registry=PRIMARY)
        self.assertEqual(subject.registry, PRIMARY)
        self.refused("skill_not_found", lambda: delivery.resolve_skill_subject(
            self.frozen(), "fresh", self.registries, registry=PRIMARY))

    def test_candidate_replaces_adopted_skill_with_only(self):
        candidate = self.root / "candidate"
        candidate.mkdir()
        (candidate / "SKILL.md").write_bytes(CANDIDATE["SKILL.md"])
        plan = self.plan(subject_candidate=candidate)
        subject = plan["subject"]
        self.assertEqual(subject.action, "removed")
        self.assertEqual(subject.deployed_digest, digest(SAMPLE))
        self.assertEqual(subject.tested_digest, digest(CANDIDATE))
        with_scratch, *_ = self.prepare("with", plan)
        without_scratch, *_ = self.prepare("without", plan)
        self.assertEqual((delivery.plugins_root(with_scratch / "config") /
                          "bundle/skills/sample/SKILL.md").read_bytes(), CANDIDATE["SKILL.md"])
        self.assertNotIn("bundle:sample", delivery.installed_skills(without_scratch / "config"))

    def test_newer_registry_bytes_never_override_deployed_default(self):
        (self.registry / "plugins/bundle/skills/sample/SKILL.md").write_bytes(FRESH["SKILL.md"])
        self.assertEqual(self.plan()["subject"].tested_digest, digest(SAMPLE))

    def test_candidate_growth_obeys_the_with_arm_budget(self):
        frozen = self.frozen()
        self.metadata["budget"]["skill_payload_bytes"] = frozen.manifest["measurements"]["skill_payload_bytes"]
        candidate = self.root / "large-candidate"
        candidate.mkdir()
        (candidate / "SKILL.md").write_bytes(SAMPLE["SKILL.md"] + b"x" * 200)
        self.refused("budget_exceeded", lambda: self.plan(subject_candidate=candidate))

    def test_guidance_candidate_replaces_only_pinned_extent(self):
        candidate = self.root / "candidate.md"
        candidate.write_bytes(b"unrelated intro\n## Bravo rule\nNew \xc3\xa9 body.\n## Unrelated\nignore\n")
        plan = self.plan(section="bravo", subject_candidate=candidate)
        subject = plan["subject"]
        self.assertNotEqual(subject.tested_digest, subject.deployed_digest)
        expected = BASE[:subject.start_byte] + "## Bravo rule\nNew é body.\n".encode() + BASE[subject.end_byte:] + b"\n" + PYTHON
        self.assertEqual(delivery.guidance_payload(plan["frozen"], subject, "with"), expected)
        self.assertNotIn(b"unrelated", delivery.guidance_payload(plan["frozen"], subject, "with"))

    def test_candidate_cannot_rename_qualified_catalog_identity(self):
        candidate = self.root / "renamed-candidate"
        candidate.mkdir()
        (candidate / "SKILL.md").write_bytes(FRESH["SKILL.md"])
        self.refused("candidate_identity_mismatch", lambda: self.plan(subject_candidate=candidate))

    def test_duplicate_source_manifest_is_refused_by_ingester(self):
        record = run_eval._in_place_record(self.plan(), "with")
        record.update(skill="sample", arm="with_skill")
        record["context"]["sources"] *= 2
        with self.assertRaises(ingest.Rejected):
            ingest.check_in_place(record, "summary.json", {"key": "sample"})

    def test_context_summary_caps_block_before_agent(self):
        frozen = self.frozen()
        manifest = dict(frozen.manifest)
        manifest["sources"] = manifest["sources"] * 65
        with mock.patch.object(context, "resolve_context", return_value=replace(frozen, manifest=manifest)):
            self.refused("context_summary_too_large", self.plan)
        self.assertFalse(self.log.exists())

    def test_executable_mode_only_candidate_is_delivered(self):
        candidate = self.root / "mode-candidate"
        for name, raw in SAMPLE.items():
            path = candidate / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        # Deployed run.sh is executable; the candidate deliberately is not.
        plan = self.plan(subject_candidate=candidate)
        scratch, *_ = self.prepare("with", plan)
        installed = delivery.plugins_root(scratch / "config") / "bundle/skills/sample/scripts/run.sh"
        self.assertEqual(installed.read_bytes(), SAMPLE["scripts/run.sh"])
        self.assertFalse(installed.stat().st_mode & 0o111)

    def test_registry_lookup_rejects_escaping_ancestor_symlink(self):
        outside = self.root / "outside-bundle/skills/escaping"
        outside.mkdir(parents=True)
        (outside / "SKILL.md").write_bytes(b"---\nname: escaping\ndescription: Escape.\n---\n")
        (self.registry / "plugins/escape").symlink_to(self.root / "outside-bundle", target_is_directory=True)
        self.refused("unsafe_path", lambda: delivery.resolve_skill_subject(self.frozen(), "escaping", self.registries))

    def test_context_manifest_names_fit_ingester_caps(self):
        frozen = self.frozen()
        manifest = dict(frozen.manifest)
        manifest["sources"] = [dict(source, bundles=[dict(bundle) for bundle in source["bundles"]])
                               for source in frozen.manifest["sources"]]
        manifest["sources"][0]["bundles"][0]["name"] = "x" * 65
        with mock.patch.object(context, "resolve_context", return_value=replace(frozen, manifest=manifest)):
            self.refused("context_summary_too_large", self.plan)

    def test_empty_guidance_remainder_record_is_bounded_and_valid(self):
        record = run_eval._in_place_record(self.plan(section="bravo"), "without")
        record.update(subject="guidance", section="bravo", arm="without_guidance")
        size = record["context"]["guidance"]["bytes"]
        record["context_subject"].update(start_char=0, end_char=size, start_byte=0, end_byte=size,
                                          bytes=size, tested_bytes=size)
        record["arm_context"].update(guidance_bytes=0, guidance_digest=hashlib.sha256(b"").hexdigest())
        ingest.check_in_place(record, "summary.json", {"key": "guidance/bravo"})

    def test_a_subject_nowhere_is_named(self):
        self.refused("skill_not_found", lambda: delivery.resolve_skill_subject(
            self.frozen(), "ghost", self.registries))

    def test_section_extent_is_unicode_exact_and_skips_fenced_headings(self):
        frozen = self.frozen()
        subject = delivery.resolve_section_subject(frozen, "alpha", self.repositories)
        text = BASE.decode("utf-8")
        start, end = text.index("## Alpha rule"), text.index("## Bravo rule")
        self.assertEqual((subject.start_char, subject.end_char), (start, end))
        self.assertEqual((subject.start_byte, subject.end_byte),
                         (len(text[:start].encode()), len(text[:end].encode())))
        # The fenced `## Not a heading` stays inside Alpha's extent.
        self.assertIn(b"## Not a heading", BASE[subject.start_byte:subject.end_byte])
        self.assertGreater(subject.record()["bytes"], subject.end_char - subject.start_char)
        self.assertEqual(subject.record()["bytes"], len(text[start:end].encode("utf-8")))
        self.refused("section_not_found", lambda: delivery.resolve_section_subject(
            frozen, "fenced", self.repositories))

    def test_an_adopted_section_is_cut_from_its_own_file_only(self):
        frozen = self.frozen()
        subject = delivery.resolve_section_subject(frozen, "bravo", self.repositories)
        self.assertEqual(subject.action, "removed")
        without = delivery.guidance_payload(frozen, subject, "without")
        self.assertEqual(delivery.guidance_payload(frozen, subject, "with"), frozen.guidance)
        cut = BASE[:subject.start_byte] + BASE[subject.end_byte:]
        self.assertEqual(without, cut + b"\n" + PYTHON)
        self.assertEqual(len(frozen.guidance) - len(without), subject.record()["bytes"])
        python = delivery.resolve_section_subject(frozen, "python", self.repositories)
        self.assertEqual(delivery.guidance_payload(frozen, python, "without"), BASE + b"\n")

    def test_an_unadopted_section_is_added_to_with_only(self):
        frozen = self.frozen()
        subject = delivery.resolve_section_subject(frozen, "docker", self.repositories)
        self.assertEqual((subject.action, subject.deployed_digest), ("added", None))
        self.assertEqual(delivery.guidance_payload(frozen, subject, "without"), frozen.guidance)
        self.assertEqual(delivery.guidance_payload(frozen, subject, "with"),
                         frozen.guidance + b"\n" + DOCKER)


class DeliveryReviewTests(Base):
    def test_finding3_qualified_subject_keeps_distinct_same_named_content(self):
        plan = self.plan("bundle:sample")
        frozen = plan["frozen"]
        distinct = replace(frozen.skills[0], bundle="two", skill="sample",
                           digest=digest(CANDIDATE), files=CANDIDATE,
                           modes={"SKILL.md": "100644"})
        plan["frozen"] = replace(frozen, skills=(*frozen.skills, distinct))
        snapshots = []
        with run_eval._in_place_pair(plan):
            for role in ("with", "without"):
                scratch = run_eval._in_place_scratch(plan)
                workspace, env, _ = run_eval._prepare_in_place(role, {}, self.seed(), plan, scratch)
                try:
                    installed = delivery.installed_skills(Path(env["CLAUDE_CONFIG_DIR"]))
                    self.assertEqual(installed["two:sample"], digest(CANDIDATE))
                    self.assertEqual("bundle:sample" in installed, role == "with")
                    snapshots.append(tree(delivery.plugins_root(Path(env["CLAUDE_CONFIG_DIR"])) / "two"))
                finally:
                    run_eval.workspace_git.release(workspace)
                    shutil.rmtree(scratch)
                    run_eval._clean_in_place_config(plan)
        self.assertEqual(snapshots[0], snapshots[1])

    def test_finding3_true_content_alias_has_its_own_error(self):
        frozen = self.frozen()
        subject = delivery.resolve_skill_subject(frozen, "sample", self.registries)
        alias = replace(frozen.skills[0], bundle="two", skill="sample", digest=digest(SAMPLE),
                        files=SAMPLE, modes=subject.modes)
        frozen = replace(frozen, skills=(*frozen.skills, alias))
        config = self.root / "cfg"
        delivery.install_plugins(config, frozen.skills, {"bundle", "two"})
        self.refused("subject_alias_present", lambda: delivery.check_skills(
            config, self.root / "workspace", delivery.expected_skills(frozen, subject, "with"),
            subject, "with"))

    def test_finding4_complete_guidance_file_and_metadata_are_exact(self):
        payload = self.frozen().guidance
        begin = delivery.guidance.BEGIN_MARK.encode() + b"\n"
        canonical = (begin + f"<!-- fleet-guidance-version: {hashlib.sha256(payload).hexdigest()[:8]} -->\n".encode()
                     + b"<!-- fleet-guidance-delivered: 0 -->\n" + payload
                     + b"<!-- END FLEET GUIDANCE -->\n")
        contaminated = (
            b"## Removed subject\n" + canonical,
            canonical.replace(b"fleet-guidance-version: ", b"fleet-guidance-version: bad"),
            canonical.replace(b"fleet-guidance-delivered: 0", b"fleet-guidance-delivered: 7"),
            canonical.replace(payload, b"<!-- fleet-guidance-uncontrolled: text -->\n" + payload),
            canonical + b"Extra subject\n")
        for n, raw in enumerate(contaminated):
            scratch = self.root / f"guidance-{n}"
            config = scratch / "config"
            config.mkdir(parents=True)
            def fake_deliver(*args, **kwargs):
                (config / "CLAUDE.md").write_bytes(raw)
                return {"returncode": 0, "installed": True, "verdict": "installed"}
            with self.subTest(case=n), mock.patch.object(delivery.guidance, "deliver", fake_deliver):
                self.refused("delivery_failed", lambda: delivery.deliver_guidance(
                    HOOK, payload, scratch=scratch, home=scratch / "home", config=config))

    def test_finding5_registration_bytes_are_equal_and_paths_resolve(self):
        plan = self.plan()
        records = []
        with run_eval._in_place_pair(plan):
            parent = plan["_pair_parent"]
            for role in ("with", "without"):
                scratch = run_eval._in_place_scratch(plan)
                workspace, env, _ = run_eval._prepare_in_place(role, {}, self.seed(), plan, scratch)
                try:
                    config = Path(env["CLAUDE_CONFIG_DIR"])
                    record = {rel: (config / rel).read_bytes() for rel in (
                        "plugins/known_marketplaces.json", "plugins/installed_plugins.json")}
                    records.append(record)
                    marketplace = json.loads(record["plugins/known_marketplaces.json"])[delivery.MARKETPLACE]
                    self.assertEqual(marketplace["source"]["path"], marketplace["installLocation"])
                    self.assertTrue((Path(marketplace["installLocation"]) / ".claude-plugin/marketplace.json").is_file())
                    installed = json.loads(record["plugins/installed_plugins.json"])["plugins"]
                    for entries in installed.values():
                        self.assertTrue((Path(entries[0]["installPath"]) / ".claude-plugin/plugin.json").is_file())
                finally:
                    run_eval.workspace_git.release(workspace)
                    shutil.rmtree(scratch)
                    run_eval._clean_in_place_config(plan)
            self.assertEqual(records[0], records[1])
            self.assertTrue(parent.is_dir())
        self.assertFalse(parent.exists())
        self.assertNotIn("_pair_parent", plan)

    def test_stale_pair_profile_fails_closed_before_agent(self):
        plan = self.plan()
        fixture = {"skill": "sample", "prompt": "do it", "context": self.metadata,
                   "registry": REGISTRY_URL, "objective_checks": []}
        with run_eval._in_place_pair(plan):
            plan["_pair_config"].mkdir()
            with mock.patch.object(run_eval, "run_agent") as agent:
                result = run_eval._run_arm(
                    "with_skill", fixture, self.seed(), self.registries, self.args(),
                    "20261007T000000Z", ("claude-test-model", None, None), plan=plan)
                self.assertEqual(result["error"]["type"], "install_collision")
                agent.assert_not_called()
        self.assertFalse(self.log.exists())

    def test_finding6_raw_line_endings_remove_exact_extent(self):
        for newline in ("\n", "\r\n", "\r"):
            raw = newline.join(("Intro é.", "## Alpha rule", "Alpha ✓.",
                                "## Bravo rule", "Bravo.", "")).encode()
            frozen = replace(self.frozen(), guidance=raw,
                             guidance_files=(("agents-md/base.md", raw),))
            subject = delivery.resolve_section_subject(frozen, "alpha", self.repositories)
            start, end = raw.index(b"## Alpha rule"), raw.index(b"## Bravo rule")
            with self.subTest(newline=repr(newline)):
                self.assertEqual((subject.start_byte, subject.end_byte), (start, end))
                self.assertEqual(subject.tested_raw, raw[start:end])
                self.assertEqual(delivery.guidance_payload(frozen, subject, "without"), raw[:start] + raw[end:])
                self.assertEqual(delivery.guidance_payload(frozen, subject, "with"), raw)


class ArmDeliveryTests(Base):
    def config_trees(self, scratch):
        return tree(scratch / "config" / "plugins" / "marketplaces" / delivery.MARKETPLACE / "plugins")

    def test_both_arms_get_byte_identical_context_but_the_subject(self):
        plan = self.plan()
        with_scratch, _, _, with_delivered = self.prepare("with", plan)
        without_scratch, _, _, without_delivered = self.prepare("without", plan)
        with_tree, without_tree = self.config_trees(with_scratch), self.config_trees(without_scratch)
        subject = {k: v for k, v in with_tree.items() if k.startswith("bundle/skills/sample/")}
        self.assertEqual(set(subject), {f"bundle/skills/sample/{k}" for k in SAMPLE})
        self.assertEqual({k: v for k, v in with_tree.items() if k not in subject}, without_tree)
        self.assertTrue(with_tree["bundle/skills/sample/scripts/run.sh"][1], "mode kept")
        self.assertEqual(with_tree["bundle/skills/sample/SKILL.md"][0], SAMPLE["SKILL.md"])
        claude = [(s / "config" / "CLAUDE.md").read_bytes() for s in (with_scratch, without_scratch)]
        self.assertEqual(claude[0], claude[1])
        self.assertIn(b"<!-- fleet-guidance-delivered: 0 -->", claude[0])
        self.assertEqual(delivery.block_body(with_scratch / "config" / "CLAUDE.md"),
                         self.frozen().guidance + b"<!-- END FLEET GUIDANCE -->\n")
        self.assertEqual(with_delivered, without_delivered)
        settings = [json.loads((s / "config" / "settings.json").read_bytes())
                    for s in (with_scratch, without_scratch)]
        self.assertEqual(settings[0], settings[1])
        self.assertEqual(settings[0], {"enabledPlugins": {f"bundle@{delivery.MARKETPLACE}": True}})
        plugin = with_scratch / "config/plugins/marketplaces" / delivery.MARKETPLACE / "plugins/bundle"
        self.assertEqual(sorted(p.name for p in plugin.iterdir()), [".claude-plugin", "skills"])
        self.assertEqual(json.loads((plugin / ".claude-plugin/plugin.json").read_bytes()),
                         {"name": "bundle", "version": delivery.PLUGIN_VERSION})

    def test_an_added_skill_reaches_with_only(self):
        plan = self.plan("fresh")
        with_scratch, *_ = self.prepare("with", plan)
        without_scratch, *_ = self.prepare("without", plan)
        with_tree, without_tree = self.config_trees(with_scratch), self.config_trees(without_scratch)
        self.assertEqual({k for k in with_tree if k not in without_tree},
                         {"spare/skills/fresh/SKILL.md"})
        self.assertEqual(set(without_tree) - set(with_tree), set())
        self.assertEqual(json.loads((with_scratch / "config/settings.json").read_bytes()),
                         json.loads((without_scratch / "config/settings.json").read_bytes()))

    def test_a_guidance_subject_changes_only_its_extent(self):
        plan = self.plan(section="bravo")
        with_scratch, *_ = self.prepare("with", plan)
        without_scratch, *_ = self.prepare("without", plan)
        self.assertEqual(self.config_trees(with_scratch), self.config_trees(without_scratch))
        subject = plan["subject"]
        with_body = delivery.block_body(with_scratch / "config/CLAUDE.md")
        without_body = delivery.block_body(without_scratch / "config/CLAUDE.md")
        self.assertEqual(len(with_body) - len(without_body), subject.end_byte - subject.start_byte)
        self.assertEqual(without_body, delivery.guidance_payload(plan["frozen"], subject, "without")
                         + b"<!-- END FLEET GUIDANCE -->\n")

    def test_a_subject_left_in_the_seed_invalidates_the_without_arm(self):
        plan = self.plan()
        seed = self.seed({"README.md": b"x\n", ".claude/skills/sample/SKILL.md": SAMPLE["SKILL.md"]})
        self.refused("subject_alias_present", lambda: self.prepare("without", plan, seed))

    def test_project_plugin_alias_is_refused(self):
        plan = self.plan()
        seed = self.seed({"README.md": b"x\n", ".claude/plugins/alias/skills/sample/SKILL.md": SAMPLE["SKILL.md"]})
        self.refused("subject_alias_present", lambda: self.prepare("without", plan, seed))

    def test_scratch_home_alias_is_refused(self):
        plan = self.plan()
        scratch, workspace, *_ = self.prepare("without", plan)
        alias = scratch / "home/.claude/skills/sample"
        alias.mkdir(parents=True)
        (alias / "SKILL.md").write_bytes(SAMPLE["SKILL.md"])
        self.refused("subject_alias_present", lambda: delivery.check_skills(
            scratch / "config", workspace, delivery.expected_skills(plan["frozen"], plan["subject"], "without"),
            plan["subject"], "without", home=scratch / "home"))

    def test_same_named_alternate_digest_invalidates_the_subject(self):
        frozen = self.frozen()
        subject = delivery.resolve_skill_subject(frozen, "sample", self.registries)
        scratch, workspace, *_ = self.prepare("without", self.plan())
        extra = delivery.plugins_root(scratch / "config") / "bundle/skills/alias"
        extra.mkdir()
        (extra / "SKILL.md").write_bytes(SAMPLE["SKILL.md"])
        expected = delivery.expected_skills(frozen, subject, "without")
        expected["bundle:alias"] = digest({"SKILL.md": SAMPLE["SKILL.md"]})
        # A front-matter alias is still the subject even with different resources.
        self.refused("subject_alias_present", lambda: delivery.check_skills(
            scratch / "config", workspace, expected, subject, "without"))

    def test_guidance_delivery_error_keeps_full_context_summary(self):
        plan = self.plan(section="bravo")
        plan["frozen"] = replace(plan["frozen"], hook=b"#!/bin/sh\necho fleet-guidance: current\n")
        fixture = {"subject": "guidance", "section": "bravo", "prompt": "do it"}
        ctx = {"plan": plan, "key": "guidance/bravo", "section": "bravo", "guidance_dir": self.guidance}
        arm = run_eval.guidance_arms({}, "with_guidance", pairing="in_place")[0]
        out = run_eval._run_guidance_arm(arm, fixture, self.seed(), ctx, self.args(), "20261007T000000Z")
        self.assertEqual(out["error"]["type"], "delivery_failed")
        path = self.root / "results/guidance/bravo/20261007T000000Z/with_guidance/summary.json"
        doc = json.loads(path.read_bytes())
        ingest.check_summary(doc, "summary.json", ingest.parse_result_path(path.relative_to(self.root / "results").as_posix()))
        self.assertEqual(doc["context_subject"]["section"], "bravo")
        self.assertFalse(self.log.exists())

    def test_project_plugin_native_metadata_alias_is_refused(self):
        seed = self.seed({"README.md": b"x\n", ".claude/plugins/alias/skills/different/SKILL.md":
                          SAMPLE["SKILL.md"] + b"Different body and digest.\n"})
        self.refused("subject_alias_present", lambda: self.prepare("without", self.plan(), seed))

    def test_installation_never_merges_into_an_existing_destination(self):
        config = self.root / "cfg"
        frozen = self.frozen()
        delivery.install_plugins(config, frozen.skills, {"bundle"})
        self.refused("install_collision", lambda: delivery.add_skill(
            config, delivery.resolve_skill_subject(frozen, "sample", self.registries)))

    def test_a_tampered_subject_is_not_removed(self):
        config = self.root / "cfg"
        frozen = self.frozen()
        delivery.install_plugins(config, frozen.skills, {"bundle"})
        (delivery.plugins_root(config) / "bundle/skills/sample/SKILL.md").write_bytes(b"changed")
        self.refused("subject_digest_mismatch", lambda: delivery.remove_skill(
            config, delivery.resolve_skill_subject(frozen, "sample", self.registries)))

    def test_a_with_arm_over_budget_is_refused_before_any_arm(self):
        self.metadata["budget"]["skill_payload_bytes"] = self.frozen().manifest[
            "measurements"]["skill_payload_bytes"]
        self.refused("budget_exceeded", lambda: self.plan("fresh"))
        self.plan()  # removal never grows an arm

    def test_lifecycle_order_and_no_second_install(self):
        plan = self.plan()
        seed = self.seed()
        events = []

        def record(name, original):
            def wrapper(*a, **k):
                if name != "commit" or (a and a[0] == "commit"):
                    events.append(name)
                return original(*a, **k)
            return wrapper

        answer = {"transcript": "done", "usage": {}, "cost_usd": 0, "num_turns": 1,
                  "duration_ms": 1, "raw": {"result": "done"}}

        def agent(workspace, prompt, arm):
            events.append("agent")
            self.assertEqual(arm["setting_sources"], "user,project")
            self.assertFalse((workspace / ".claude").exists())
            return answer

        fixture = {"skill": "sample", "prompt": "do it", "context": self.metadata,
                   "registry": REGISTRY_URL, "objective_checks": []}
        patches = [mock.patch.object(run_eval.seed_prep, "prepare_seed",
                                     record("strip", run_eval.seed_prep.prepare_seed)),
                   mock.patch.object(run_eval, "run_setup", record("setup", run_eval.run_setup)),
                   mock.patch.object(run_eval.seed_prep, "seed_guard",
                                     record("seed_guard", run_eval.seed_prep.seed_guard)),
                   mock.patch.object(run_eval, "_git", record("commit", run_eval._git)),
                   mock.patch.object(delivery, "install_plugins",
                                     record("deliver_skills", delivery.install_plugins)),
                   mock.patch.object(delivery, "deliver_guidance",
                                     record("deliver_guidance", delivery.deliver_guidance)),
                   mock.patch.object(delivery, "remove_skill",
                                     record("remove_subject", delivery.remove_skill)),
                   mock.patch.object(delivery, "add_skill", record("add_subject", delivery.add_skill)),
                   mock.patch.object(run_eval, "install_skill", side_effect=AssertionError),
                   mock.patch.object(run_eval, "run_agent", side_effect=agent)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        for arm in ("without_skill", "with_skill"):
            events.clear()
            out = run_eval._run_arm(arm, fixture, seed, self.registries, self.args(), "20261007T000000Z",
                                    ("claude-test-model", None, None),
                                    out_dir=self.root / "results" / arm, plan=plan)
            self.assertIsNone(out["error"], out)
            subject_step = ["remove_subject"] if arm == "without_skill" else []
            self.assertEqual(events, ["strip", "setup", "seed_guard", "commit", "deliver_skills",
                                      "deliver_guidance", *subject_step, "agent"])
            summary = json.loads((self.root / "results" / arm / "summary.json").read_bytes())
            self.assertEqual(summary["pairing"], "in_place")
            self.assertEqual(summary["context_subject"]["action"], "removed")
        self.assertEqual(list(Path(tempfile.gettempdir()).glob(f"{run_eval.ARM_WORKSPACE_PREFIX}with_skill-*"))
                         + list(Path(tempfile.gettempdir()).glob(
                             f"{run_eval.ARM_WORKSPACE_PREFIX}without_skill-*")), [])

    def test_in_place_without_names_may_deliver_isolation_ones_may_not(self):
        arms = run_eval.guidance_arms({}, "both", pairing="in_place")
        self.assertEqual([(a["name"], a["mode"]) for a in arms],
                         [("with_guidance", "full"), ("without_guidance", "full-minus-section")])
        with self.assertRaises(guidance_error()):
            run_eval.guidance_arms({"arms": {"without_guidance": {"mode": "full-minus-section"}}},
                                   "both")
        with self.assertRaises(guidance_error()):
            run_eval.guidance_arms({}, "both", ablation=True, pairing="in_place")

    def test_hosted_detection(self):
        self.assertFalse(delivery.hosted({"CLAUDE_CODE_ENTRYPOINT": "cli"}))
        self.assertTrue(delivery.hosted({"CLAUDE_CODE_REMOTE_SESSION_ID": "s"}))
        self.assertTrue(delivery.hosted({"CLAUDE_CODE_ENTRYPOINT": "remote_mobile"}))
        self.refused("in_place_local_unsupported", lambda: delivery.require_hosted({}))


class RealHookTests(Base):
    def test_the_real_hook_delivers_identical_blocks_to_both_arms(self):
        hook = (ROOT / ".claude/hooks/fleet-memory.sh").read_bytes()
        self.guidance_sha = self.commit(self.guidance, {".claude/hooks/fleet-memory.sh": hook})
        self.git(self.guidance, "update-ref", "refs/remotes/origin/main", self.guidance_sha)
        self.metadata["guidance_revision"] = self.guidance_sha
        plan = self.plan()
        scratches = [self.prepare(role, plan)[0] for role in ("with", "without")]
        blocks = [(s / "config/CLAUDE.md").read_bytes() for s in scratches]
        self.assertEqual(blocks[0], blocks[1])
        self.assertIn(self.frozen().guidance, blocks[0])


class EndToEndTests(Base):
    TS = "20261007T000000Z"

    def setUp(self):
        super().setUp()
        self.results = self.root / "results"
        self.registry_dirs = {}
        for name in ("example-site-platform", "example-site-a", "acsi-agentskills-private"):
            path = self.root / "registries" / name
            path.mkdir(parents=True)
            self.registry_dirs[name] = path

    def fixture_dir(self, **extra):
        directory = self.root / "evals" / "sample"
        (directory / "seed").mkdir(parents=True, exist_ok=True)
        (directory / "seed" / "README.md").write_text("x\n", encoding="utf-8")
        body = {"skill": "sample", "registry": REGISTRY_URL, "prompt": "do it",
                "model": "claude-test-model", "env": {"REC_LOG": str(self.log)},
                "objective_checks": [{"id": "seed-kept", "description": "d",
                                      "type": "files_unchanged", "paths": ["README.md"]}],
                "context": self.metadata, **extra}
        (directory / "fixture.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")
        return directory

    def run_main(self, directory, *flags, hosted=True, extra_env=None):
        env = dict(self.env)
        env["SKILLS_EVALS_REGISTRIES"] = ",".join(f"{k}={v}" for k, v in self.registry_dirs.items())
        env["CLAUDE_CODE_ENABLE_CFC"] = "1"
        if hosted:
            env["CLAUDE_CODE_REMOTE_SESSION_ID"] = "session_test"
        env.update(extra_env or {})
        repos = [f"--context-repo={k}={v}" for k, v in self.repositories.items()]
        return subprocess.run(
            [sys.executable, "harness/run_eval.py", str(directory),
             "--registry", f"acsi-agentskills={self.registry}", "--results-dir", str(self.results),
             "--timeout", "60", "--no-judge", *repos, *flags],
            capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=600)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] \
            if self.log.exists() else []

    def summary(self, arm, key="sample", ts=None):
        return json.loads((self.results / key / (ts or self.TS) / arm / "summary.json").read_bytes())

    def assert_ingests(self, path):
        doc = json.loads(path.read_bytes())
        ingest.check_summary(doc, path.name, ingest.parse_result_path(
            path.relative_to(self.results).as_posix()))
        return doc

    def assert_arm_isolation(self, call, config):
        argv = call["argv"]
        settings = json.loads(run_eval_flag(argv, "--settings"))
        sandbox = settings["sandbox"]
        self.assertIs(sandbox["enabled"], True)
        self.assertIs(sandbox["failIfUnavailable"], True)
        self.assertIs(sandbox["allowUnsandboxedCommands"], False)
        self.assertEqual(sandbox["network"], run_eval.arm_sandbox_settings()["sandbox"]["network"])
        denied = sandbox["filesystem"]["denyRead"]
        scratch_home = Path(call["env"]["HOME"])
        for path in (self.home, self.consumer, self.registry, self.guidance, scratch_home, config):
            self.assertIn(str(path.resolve()), denied)
        rules = settings["permissions"]["deny"]
        patterns = [rule[len("Read(/"):-1].removesuffix("/**")
                    for rule in rules if rule.startswith("Read(/")]
        def covered(target):
            return any(len(pattern.split("/")) == len(path.parts) and all(
                fnmatch.fnmatchcase(part.lower(), glob.lower())
                for part, glob in zip(path.parts[1:], pattern.split("/")[1:]))
                       for pattern in patterns for path in (target, *target.parents))
        for protected in (scratch_home / "private", config / "settings.json", config / "CLAUDE.md",
                          config / "plugins/private", self.home / ".claude/.credentials.json"):
            self.assertTrue(covered(protected), f"Read deny absent for {protected}")
        own_session = config / "projects" / run_eval._munged_project_name(Path(call["cwd"]))
        self.assertFalse(covered(own_session / "session.jsonl"), "own follow-up output must stay readable")
        self.assertEqual(tree(self.home / ".claude"), self.source_profile)
        # Other arms' scratch profiles: the harness's TMPDIR prefixes.
        self.assertTrue(any(run_eval.ARM_WORKSPACE_PREFIX in rule for rule in rules), rules[:5])
        self.assertIn(str(config), sandbox["filesystem"]["denyWrite"])
        self.assertIn(str(scratch_home), sandbox["filesystem"]["denyWrite"])
        self.assertIn(str(config.parent), sandbox["filesystem"]["denyWrite"])
        self.assertFalse(any(run_eval._within(Path(call["cwd"]), Path(path))
                             for path in sandbox["filesystem"]["denyWrite"]))
        self.assertIn(f"Edit(/{config})", rules)
        self.assertIn(f"Edit(/{config}/**)", rules)
        self.assertEqual(run_eval_flag(argv, "--disallowedTools"), "WebFetch,WebSearch")
        self.assertIn("--no-chrome", argv)
        self.assertIn("--strict-mcp-config", argv)
        self.assertEqual(run_eval_flag(argv, "--setting-sources"), "user,project")
        self.assertIsNone(call["env"]["CLAUDE_CODE_ENABLE_CFC"])
        self.assertIsNone(call["env"]["CLAUDE_CODE_REMOTE_SESSION_ID"])
        self.assertEqual(call["env"]["CLAUDE_CONFIG_DIR"], str(config))
        self.assertNotEqual(Path(call["env"]["HOME"]), self.home)

    def test_a_local_in_place_run_fails_closed_before_any_cli(self):
        for arm in ("both", "objective-only"):
            proc = self.run_main(self.fixture_dir(), "--arm", arm, "--pairing", "in_place",
                                 "--timestamp", self.TS, hosted=False)
            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
            self.assertIn("in_place_local_unsupported", proc.stdout)
        self.assertEqual(self.calls(), [])
        self.assertFalse(self.results.exists())
        self.assertFalse(self.version_log.exists(), "local refusal must precede --version too")

    def test_agent_reads_delivered_resources_but_not_private_profile(self):
        fixture = self.fixture_dir(env={"REC_LOG": str(self.log), "REC_MODE": "read_resources"})
        proc = self.run_main(fixture, "--arm", "with_skill", "--pairing", "in_place",
                             "--timestamp", self.TS)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        (call,) = self.calls()
        reads = call["resource_reads"]
        resource = next(value for path, value in reads.items() if path.endswith("scripts/run.sh"))
        self.assertIs(resource["allowed"], True)
        self.assertEqual(resource["bytes"], SAMPLE["scripts/run.sh"].hex())
        self.assertTrue(all(not value["allowed"] for path, value in reads.items()
                            if not path.endswith("scripts/run.sh")))
        self.assert_arm_isolation(call, Path(call["env"]["CLAUDE_CONFIG_DIR"]))

    def test_plugin_read_exceptions_refuse_unverified_content_before_spawn(self):
        plan = self.plan()
        for mutation in ("symlink", "fifo", "hooks"):
            with self.subTest(mutation=mutation):
                _, workspace, env, _ = self.prepare("with", plan)
                bundle = delivery.plugins_root(Path(env["CLAUDE_CONFIG_DIR"])) / "bundle"
                resource = bundle / "skills/sample/scripts/run.sh"
                if mutation == "hooks":
                    (bundle / ".claude-plugin/plugin.json").write_text(json.dumps({
                        "name": "bundle", "version": delivery.PLUGIN_VERSION, "hooks": {}}))
                else:
                    resource.unlink()
                    if mutation == "symlink":
                        resource.symlink_to(self.home / ".claude/.credentials.json")
                    else:
                        os.mkfifo(resource)
                result = run_eval.run_agent(workspace, "do it", {
                    "name": "with_skill", "env_override": env,
                    "verified_plugin_roots": [bundle]})
                self.assertEqual(result["error"], "read_rules_unsafe")
        self.assertEqual(self.calls(), [])

    def test_hosted_in_place_skill_pair_end_to_end(self):
        proc = self.run_main(self.fixture_dir(followups=["and again"]), "--arm", "both",
                             "--pairing", "in_place", "--timestamp", self.TS)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        calls = self.calls()
        self.assertEqual(len(calls), 4)
        by_subject = {}
        for call in calls:
            present = "bundle/skills/sample/SKILL.md" in call["plugins"]
            by_subject.setdefault(present, []).append(call)
        self.assertEqual(len(by_subject), 2)
        self.assertEqual(len({call["env"]["CLAUDE_CONFIG_DIR"] for call in calls}), 1)
        for turns in by_subject.values():
            self.assertEqual(len(turns), 2)
            self.assertIn("--resume", turns[1]["argv"])
            for call in turns:
                self.assert_arm_isolation(call, Path(call["env"]["CLAUDE_CONFIG_DIR"]))
                self.assertEqual(call["workspace_skills"], [])
        with_call, without_call = sorted((t[0] for t in by_subject.values()),
                                         key=lambda c: "bundle/skills/sample/SKILL.md" not in c["plugins"])
        self.assertEqual(with_call["claude_md"], without_call["claude_md"])
        self.assertEqual(with_call["registrations"], without_call["registrations"])
        self.assertEqual({k: v for k, v in with_call["plugins"].items()
                          if not k.startswith("bundle/skills/sample/")}, without_call["plugins"])
        arm_contexts = []
        for arm, role in (("with_skill", "with"), ("without_skill", "without")):
            doc = self.assert_ingests(self.results / "sample" / self.TS / arm / "summary.json")
            self.assertIsNone(doc["error"])
            self.assertEqual((doc["pairing"], doc["role"]), ("in_place", role))
            self.assertEqual(doc["context"]["digest"], self.frozen().digest)
            self.assertEqual(doc["context_subject"]["action"], "removed")
            self.assertEqual(doc["context_subject"]["deployed_digest"], digest(SAMPLE))
            self.assertEqual(doc["arm_context"]["subject_present"], role == "with")
            self.assertLess(len(json.dumps(doc)), ingest.SIZE_CAPS["summary.json"])
            arm_contexts.append(doc["arm_context"])
        # #70, round 5: the non-subject skills are one set across the pair
        # (compute_gates pairs arms on it), though the arms' full sets differ.
        with_ctx, without_ctx = arm_contexts
        self.assertEqual(with_ctx["others_digest"], without_ctx["others_digest"])
        self.assertNotEqual(with_ctx["skills_digest"], without_ctx["skills_digest"])
        self.assertEqual(without_ctx["others_digest"], without_ctx["skills_digest"])
        self.assertEqual(sorted(Path(tempfile.gettempdir()).glob(
            f"{run_eval.ARM_WORKSPACE_PREFIX}with*_skill-*")), [])

    def test_the_agent_may_not_write_its_scratch_profile(self):
        directory = self.fixture_dir(followups=["again"], env={"REC_LOG": str(self.log), "REC_MODE": "write_profile"})
        proc = self.run_main(directory, "--arm", "without_skill", "--pairing", "in_place",
                             "--timestamp", self.TS)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertEqual(self.summary("without_skill")["error"]["type"], "agent_wrote_agent_config")
        self.assertEqual(len(self.calls()), 1)

    def test_candidate_flags_replace_only_subject_and_deny_source(self):
        candidate = self.root / "candidate-cli"
        candidate.mkdir()
        (candidate / "SKILL.md").write_bytes(CANDIDATE["SKILL.md"])
        proc = self.run_main(self.fixture_dir(), "--arm", "both", "--pairing", "in_place",
                             "--timestamp", self.TS, "--skill-registry", PRIMARY,
                             "--skill-bundle", "bundle", "--subject-candidate", str(candidate))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        calls = self.calls()
        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assert_arm_isolation(call, Path(call["env"]["CLAUDE_CONFIG_DIR"]))
            settings = json.loads(run_eval_flag(call["argv"], "--settings"))
            self.assertIn(str(candidate), settings["sandbox"]["filesystem"]["denyRead"])
        for arm in ("with_skill", "without_skill"):
            doc = self.assert_ingests(self.results / "sample" / self.TS / arm / "summary.json")
            self.assertEqual(doc["context_subject"]["tested_digest"], digest(CANDIDATE))
            self.assertEqual(doc["context_subject"]["deployed_digest"], digest(SAMPLE))
        self.assertEqual(calls[0]["plugins"]["bundle/skills/sample/SKILL.md"],
                         hashlib.sha256(CANDIDATE["SKILL.md"]).hexdigest())
        self.assertNotIn("bundle/skills/sample/SKILL.md", calls[1]["plugins"])

    def test_real_work_subject_any_summaries_fully_ingest(self):
        for selector, value in (("--skill", "sample"), ("--section", "bravo")):
            directory = self.fixture_dir(subject="any")
            body = yaml.safe_load((directory / "fixture.yaml").read_bytes())
            body.pop("skill")
            (directory / "fixture.yaml").write_text(yaml.safe_dump(body))
            self.results = self.root / ("results-skill" if selector == "--skill" else "results-guidance")
            proc = self.run_main(directory, "--arm", "both", "--pairing", "in_place",
                                 "--timestamp", self.TS, selector, value)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            paths = list(self.results.rglob("summary.json"))
            self.assertEqual(len(paths), 2)
            for path in paths:
                with self.subTest(selector=selector, path=path):
                    doc = self.assert_ingests(path)
                    self.assertEqual(doc["fixture"], "sample")
                    self.assertEqual(doc["n"], 1)
                    self.assertEqual(doc["guidance_violations"]["schema_version"], 1)

    def test_alternate_guidance_fixture_path_is_strict(self):
        prefix = f"guidance/bravo/sample/{self.TS}"
        parts = ingest.parse_result_path(f"{prefix}/with_guidance/summary.json")
        self.assertEqual((parts["key"], parts["fixture"]), ("guidance/bravo", "sample"))
        for path in (f"{prefix}/second/with_guidance/summary.json",
                     f"guidance/bravo/trial-1/{self.TS}/with_guidance/summary.json",
                     f"guidance/bravo/../{self.TS}/with_guidance/summary.json"):
            with self.subTest(path=path), self.assertRaises(ingest.Rejected):
                ingest.parse_result_path(path)

    def test_second_turn_profile_write_stops_a_third_turn(self):
        directory = self.fixture_dir(followups=["again", "third"],
                                     env={"REC_LOG": str(self.log), "REC_MODE": "write_profile_second"})
        proc = self.run_main(directory, "--arm", "without_skill", "--pairing", "in_place",
                             "--timestamp", self.TS)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertEqual(len(self.calls()), 2)
        self.assertEqual(self.summary("without_skill")["error"]["type"], "agent_wrote_agent_config")

    def test_unresolvable_context_is_exit_two_before_any_cli(self):
        self.metadata["guidance_revision"] = "f" * 40
        proc = self.run_main(self.fixture_dir(), "--arm", "both", "--pairing", "in_place",
                             "--timestamp", self.TS)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("guidance_unproven", proc.stdout)
        self.assertEqual(self.calls(), [])

    def test_isolation_is_unchanged(self):
        proc = self.run_main(self.fixture_dir(), "--arm", "both", "--timestamp", self.TS)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        calls = {tuple(c["workspace_skills"]): c for c in self.calls()}
        self.assertEqual(set(calls), {("sample",), ()})
        for call in calls.values():
            self.assertEqual(run_eval_flag(call["argv"], "--setting-sources"), "project")
            self.assertIsNone(call["env"]["CLAUDE_CONFIG_DIR"])
        for arm in ("with_skill", "without_skill"):
            doc = self.assert_ingests(self.results / "sample" / self.TS / arm / "summary.json")
            self.assertFalse(set(ingest.IN_PLACE_KEYS) & set(doc))

    def test_hosted_in_place_guidance_pair(self):
        directory = self.root / "evals" / "guidance-fixture"
        (directory / "seed").mkdir(parents=True)
        (directory / "seed" / "README.md").write_text("x\n", encoding="utf-8")
        (directory / "fixture.yaml").write_text(yaml.safe_dump({
            "subject": "guidance", "section": "bravo", "prompt": "do it", "followups": ["again"],
            "model": "claude-test-model", "env": {"REC_LOG": str(self.log)},
            "objective_checks": [], "context": self.metadata}), encoding="utf-8")
        proc = self.run_main(directory, "--arm", "both", "--pairing", "in_place",
                             "--guidance", str(self.root / "missing-live-guidance"), "--timestamp", self.TS)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        calls = self.calls()
        self.assertEqual(len(calls), 4)
        for call in calls:
            self.assert_arm_isolation(call, Path(call["env"]["CLAUDE_CONFIG_DIR"]))
        self.assertEqual(calls[0]["plugins"], calls[2]["plugins"])
        self.assertEqual(calls[0]["registrations"], calls[2]["registrations"])
        self.assertNotEqual(calls[0]["claude_md"], calls[2]["claude_md"])
        run_dir = self.results / "guidance" / "bravo" / self.TS
        self.assertTrue(run_dir.is_dir(), "guidance must honor the requested timestamp")
        for arm, role in (("with_guidance", "with"), ("without_guidance", "without")):
            path = run_dir / arm / "summary.json"
            doc = json.loads(path.read_bytes())
            ingest.check_summary(doc, path.name, ingest.parse_result_path(
                path.relative_to(self.results).as_posix()))
            self.assertEqual(doc["role"], role)
            self.assertEqual(doc["context_subject"]["kind"], "guidance")
            self.assertIsNone(doc["guard"])

    def test_the_ingester_rejects_inconsistent_in_place_records(self):
        self.run_main(self.fixture_dir(), "--arm", "both", "--pairing", "in_place",
                      "--timestamp", self.TS)
        path = self.results / "sample" / self.TS / "with_skill" / "summary.json"
        parts = ingest.parse_result_path(path.relative_to(self.results).as_posix())
        good = json.loads(path.read_bytes())
        for edit in (lambda d: d.pop("context"),
                     lambda d: d.update(role="without"),
                     lambda d: d.update(pairing="isolation"),
                     lambda d: d["arm_context"].update(subject_present=False),
                     lambda d: d["context_subject"].update(deployed_digest=None),
                     lambda d: d["context_subject"].update(skill="other"),
                     lambda d: d["context"].update(digest="x" * 64),
                     lambda d: d["context"]["guidance"].update(bytes=True),
                     lambda d: d["context"]["guidance"].update(sections=[{}])):
            doc = json.loads(json.dumps(good))
            edit(doc)
            with self.subTest(doc=str(doc)[:60]), self.assertRaises(ingest.Rejected):
                ingest.check_summary(doc, "summary.json", parts)


def run_eval_flag(argv, flag):
    indexes = [i for i, arg in enumerate(argv) if arg == flag]
    if len(indexes) != 1:
        raise AssertionError(f"{flag} appears {len(indexes)} times")
    return argv[indexes[0] + 1]


class ManagedPolicyTests(Base):
    def test_an_in_place_arm_still_refuses_a_managed_policy(self):
        plan = self.plan()
        fixture = {"skill": "sample", "prompt": "do it", "context": self.metadata,
                   "objective_checks": [], "env": {"REC_LOG": str(self.log)}}
        with mock.patch.object(run_eval, "managed_sandbox_refusal", return_value="x: refused"):
            out = run_eval._run_arm("with_skill", fixture, self.seed(), self.registries,
                                    self.args(), "20261007T000000Z",
                                    ("claude-test-model", None, None),
                                    out_dir=self.root / "results" / "with_skill", plan=plan)
        self.assertEqual(out["error"]["type"], "managed_sandbox_policy")
        self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
