"""The first real-work fixtures under evals/real-work/ (the fixtures DESIGN's
"smallest next PR 4"): each is red on its seed and green with its pull
request's fix applied, scored by its pull request's own hidden tests.

Offline: the scorer's network probe is mocked off and nothing is fetched.
The checkers need each seed's npm dependencies installed from the seed's own
lockfile first (`npm ci` in the directory each fixture's `deps:` names; CI's
test job does it), exactly as the browser-testing fixture's tests need theirs.
"""

from __future__ import annotations

import contextlib
import hashlib
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

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "harness"))
import answer_leak  # noqa: E402
import run_eval  # noqa: E402
from scorers import commands  # noqa: E402
import seed_prep  # noqa: E402

# The kit ships no real-work fixtures (DECISIONS X9): the tests that need a
# real pull request's seed, fix or results were removed with them, and the
# fixture-shape, checker, install and Dependabot wiring checks read the
# invented test-only fixture, laid out as it would sit under evals/real-work/.
REAL_WORK = REPO / "test" / "fixtures" / "evals" / "real-work"
SCHEDULE = REPO / "evals" / "scheduled.yml"
sys.path.insert(0, str(REPO / "scripts"))
import scaffold_real_work as scaffold  # noqa: E402

# Each checker file's git blob id in its pull request's merge commit, read by
# hand with `git ls-tree <merge> <path>`: the overlay is that file, byte for
# byte. These are the fixtures a person reviewed; routine-scaffold-gate.yml
# makes the same check from trusted Git bytes for every later one
# (scaffold_real_work.gate_checker), so a scaffolded fixture needs no entry.
CHECKER_BLOBS: dict = {}
# Paths a seed must not carry: the agent context the guidance and skill A/B
# withholds, and the subject under evaluation itself (the owner, 2026-10-06: "Strip
# subject + trim").
EVALUATED_PATHS = (".claude", "skills.lock", "agents-md", "skills", ".claude-plugin")
# Files over this size were trimmed unless something needs them: the lockfile
# `deps:` installs from, and AGENTS.md's kept repository-specific section.
TRIM_BYTES = 100 * 1024
KEPT_LARGE = {"package-lock.json", "AGENTS.md"}
# The one description a seed's plugin manifest may carry. The manifest stays (a
# kept test reads its version) but its upstream description lists what the
# stripped skills/ directory does, which is the subject leaking back in.
NEUTRAL_MANIFEST_DESCRIPTION = "example-site-platform: a Jekyll + Decap CMS + AWS site platform."
MANIFESTS = ("plugin.json", ".claude-plugin/plugin.json")
# The ecosystem Dependabot reads each seed manifest as.
DEPENDABOT_MANIFESTS = {"package.json": "npm", "package-lock.json": "npm",
                        "Gemfile": "bundler", "Gemfile.lock": "bundler"}
DEPENDABOT_CONFIG = REPO / ".github" / "dependabot.yml"


def blob_id(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


# Installed dependency trees (CI's install steps put them in the seeds), not
# seed content.
INSTALLED_DIRS = ("node_modules", "vendor")


def seed_files(seed: Path):
    for directory, dirnames, filenames in os.walk(seed):
        dirnames[:] = [d for d in dirnames if d not in INSTALLED_DIRS]
        for name in filenames:
            yield Path(directory) / name


class RealWorkFixtureTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("scorers.commands._sandbox_prefix", return_value=([], "unavailable"))
        patcher.start()
        self.addCleanup(patcher.stop)
        sandbox = mock.patch.object(commands, "_run_sandboxed",
                                    side_effect=lambda prefix, *args: commands._run_command(*args))
        sandbox.start()
        self.addCleanup(sandbox.stop)

    def fixtures(self):
        names = sorted(p.name for p in REAL_WORK.iterdir() if (p / "fixture.yaml").is_file())
        self.assertLessEqual(set(CHECKER_BLOBS), set(names))
        for name in names:
            yield name, REAL_WORK / name, run_eval.load_fixture(REAL_WORK / name)

    def test_each_fixture_loads_as_a_subject_agnostic_draft_or_promoted(self):
        schedule = {row["path"]: row["readiness"] for row in
                    yaml.safe_load(SCHEDULE.read_text(encoding="utf-8"))["fixtures"]}
        for name, directory, fixture in self.fixtures():
            with self.subTest(fixture=name):
                path = directory / "fixture.yaml"
                self.assertEqual(fixture["subject"], "any")
                if fixture["draft"] is not True:
                    # Only `scaffold_real_work.py promote` clears the key, and
                    # it schedules the fixture with its own readiness note.
                    self.assertIs(fixture["draft"], False)
                    self.assertTrue(schedule.get(f"evals/real-work/{name}", "")
                                    .startswith(scaffold.PROMOTED_NOTE), name)
                self.assertIs(fixture["strip_agent_context"], True)
                self.assertNotIn("skill", fixture)
                self.assertNotIn("section", fixture)
                run_eval.apply_runtime_subject(fixture, None, None, "objective-only", path)
                answer_leak.validate_fixture(fixture, path)
                seed_prep.validate_fixture(fixture, directory)
                [check] = fixture["objective_checks"]
                self.assertEqual((check["type"], check["overlay"]), ("repo_tests", "checker"))

    def test_each_checker_is_the_pull_requests_own_test_file(self):
        for name, directory, _ in self.fixtures():
            with self.subTest(fixture=name):
                checker = directory / "checker"
                found = {p.relative_to(checker).as_posix(): blob_id(p)
                         for p in checker.rglob("*") if p.is_file()}
                self.assertTrue(found)
                if name in CHECKER_BLOBS:
                    self.assertEqual(found, CHECKER_BLOBS[name])


    def test_ci_installs_exactly_the_fixtures_dependencies(self):
        workflow = yaml.safe_load((REPO / ".github" / "workflows" / "ci.yml")
                                  .read_text(encoding="utf-8"))
        [step] = [s for s in workflow["jobs"]["suite"]["steps"]
                  if s.get("name") == "Install real-work fixture dependencies"]
        self.assertEqual(step["working-directory"], "skills-evals/evals/real-work")
        self.assertEqual(step["if"], "steps.salient.outputs.run == 'true'")
        self.assert_derived_loop(step["run"], "npm")
        self.assertIn("npm ci --ignore-scripts --no-audit --no-fund", step["run"])
        [step] = [s for s in workflow["jobs"]["suite"]["steps"]
                  if s.get("name") == "Install real-work fixture gems"]
        self.assertEqual(step["working-directory"], "skills-evals/evals/real-work")
        self.assertEqual(step["if"], "steps.salient.outputs.run == 'true'")
        self.assert_derived_loop(step["run"], "bundler")
        for variable in ("BUNDLE_PATH=vendor/bundle", "BUNDLE_FROZEN=true",
                         "BUNDLE_DISABLE_SHARED_GEMS=true"):
            self.assertIn(variable, step["run"])

    def assert_derived_loop(self, run, manager):
        # The step installs exactly what deps-dirs lists, and fails when the
        # list cannot be read (an assignment, not a bare `for ... in $(...)`).
        lines = [line.strip() for line in run.splitlines()]
        self.assertIn('dirs="$(python3 ../../scripts/scaffold_real_work.py deps-dirs '
                      f'--manager {manager})"', lines)
        self.assertIn("for dir in $dirs; do", lines)
        self.assertIn("set -euo pipefail", lines)
        wanted = sorted(f"{name}/seed" + ("" if entry["dir"] == "." else f"/{entry['dir']}")
                        for name, _, fixture in self.fixtures()
                        for entry in fixture.get("deps", ()) if entry["manager"] == manager)
        self.assertTrue(wanted)
        self.assertEqual(scaffold.deps_dirs(manager, REAL_WORK), wanted)


    @staticmethod
    def dependabot_glob(pattern: str) -> re.Pattern:
        # Dependabot's `directories:` globbing: `**` spans segments, `*` one.
        parts = re.split(r"(\*\*|\*)", pattern)
        return re.compile("".join(
            ".*" if part == "**" else "[^/]*" if part == "*" else re.escape(part)
            for part in parts))

    def test_dependabot_ignores_every_seed_manifest(self):
        # Dependabot security updates apply to any lockfile in the repository,
        # not only the directories dependabot.yml lists (skills-evals#312 bumped
        # a seed's package-lock.json, and the auto-merge allowlist would have
        # landed it). `exclude-paths` is version-updates-only, so what holds
        # security updates off is an entry per ecosystem over the seeds with
        # `ignore: dependency-name: "*"` (`update-types` would not, per the
        # options reference), and no `versions:` that narrows it.
        config = yaml.safe_load(DEPENDABOT_CONFIG.read_text(encoding="utf-8"))
        covered = {}
        for entry in config["updates"]:
            if entry["package-ecosystem"] == "github-actions":
                continue
            ecosystem = entry["package-ecosystem"]
            self.assertNotIn("directory", entry, "a seeds entry uses `directories:`")
            self.assertEqual(entry["ignore"], [{"dependency-name": "*"}])
            self.assertEqual(entry["cooldown"], {"default-days": 7, "semver-major-days": 30})
            covered.setdefault(ecosystem, []).extend(
                self.dependabot_glob(pattern) for pattern in entry["directories"])
        self.assertEqual(sorted(covered), ["bundler", "npm"])
        found = 0
        for directory, dirnames, filenames in os.walk(REAL_WORK):
            dirnames[:] = [d for d in dirnames if d != "node_modules"]
            for filename in filenames:
                if filename not in DEPENDABOT_MANIFESTS:
                    continue
                found += 1
                where = "/evals/real-work/" + Path(directory).relative_to(REAL_WORK).as_posix()
                with self.subTest(manifest=f"{where}/{filename}"):
                    self.assertTrue(
                        any(glob.fullmatch(where)
                            for glob in covered[DEPENDABOT_MANIFESTS[filename]]),
                        f"no dependabot.yml entry ignores {where}")
        self.assertGreaterEqual(found, 4)

    def workspace(self, directory: Path, fixture: dict, fixed: bool) -> Path:
        """The seed as the agent would get it, its dependencies already
        installed (symlinked from the seed), and for `fixed` the pull
        request's own fix applied."""
        seed = directory / "seed"
        installed = {"npm": ("node_modules", "npm ci"),
                     "bundler": (seed_prep.BUNDLE_PATH, "bundle install")}
        for entry in fixture.get("deps", ()):
            tree, command = installed[entry["manager"]]
            modules = seed / entry["dir"] / tree
            self.assertTrue(modules.is_dir(),
                            f"Install {modules.parent} with {command} before running the suite")
        owned = tempfile.TemporaryDirectory(prefix="real-work-")
        self.addCleanup(owned.cleanup)
        workspace = Path(owned.name) / "ws"
        shutil.copytree(seed, workspace, ignore=shutil.ignore_patterns(*INSTALLED_DIRS))
        for entry in fixture.get("deps", ()):
            tree = installed[entry["manager"]][0]
            (workspace / entry["dir"] / tree).parent.mkdir(parents=True, exist_ok=True)
            (workspace / entry["dir"] / tree).symlink_to(
                seed / entry["dir"] / tree, target_is_directory=True)
        seed_prep.prepare_seed(workspace, fixture)
        self.assertIsNone(seed_prep.seed_guard(workspace, fixture))
        if fixed:
            subprocess.run(["git", "apply", "--whitespace=nowarn",
                            str(directory / "solution.patch")],
                           cwd=workspace, check=True, timeout=30,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return workspace

    def objective_only(self, directory: Path, workspace: Path):
        out = io.StringIO()
        argv = ["run_eval.py", str(directory), "--arm", "objective-only",
                "--workspace", str(workspace)]
        with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(out):
            code = run_eval.main()
        return code, out.getvalue()


if __name__ == "__main__":
    unittest.main()
