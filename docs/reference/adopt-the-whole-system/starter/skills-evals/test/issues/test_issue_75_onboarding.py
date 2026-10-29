"""Issue #75: offline onboarding and the two-fixture layout."""

from __future__ import annotations

import copy
import glob
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[2]
FAMILY = ROOT / "evals/github-actions-repo-settings"
ONBOARDING = FAMILY / "onboarding"
SEED = ONBOARDING / "seed"
sys.path.insert(0, str(ROOT / "harness"))
import context  # noqa: E402
import run_eval  # noqa: E402
from scorers import objective  # noqa: E402

CHECK_IDS = ("config-parses", "inherited-defaults", "repository-roster",
             "no-write-attempted", "instrument-unchanged")
NEW_REPO = {"name": "example-org/new-service", "manage": ["all"]}


def scores(fixture: dict, workspace: Path) -> dict[str, bool]:
    return {row["id"]: row["passed"] for row in objective.run_checks(
        fixture, str(workspace), str(SEED))}


class TestIssue75Onboarding(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="issue-75-onboarding-")
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Path(self.tmp.name) / "workspace"
        # The seed has no Git metadata or credentials; its only executable is
        # the shared fake gh. Copies stay offline.
        shutil.copytree(SEED, self.workspace)
        self.fixture = run_eval.load_fixture(ONBOARDING)
        self.config = self.workspace / "repo-settings.yml"

    def _document(self) -> dict:
        return yaml.safe_load(self.config.read_text(encoding="utf-8"))

    def _write(self, document: dict) -> None:
        self.config.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    def _complete(self) -> None:
        document = self._document()
        document["repos"].append(NEW_REPO)
        self._write(document)

    def test_layout_context_and_draft_gate(self):
        self.assertEqual(run_eval.resolve_fixture_dirs(FAMILY, None),
                         [FAMILY / "drift-diagnosis", ONBOARDING])
        drift = run_eval.load_fixture(FAMILY / "drift-diagnosis")
        for fixture in (drift, self.fixture):
            metadata = fixture["context"]
            context.validate_context(fixture, "fixture.yaml")
            self.assertEqual(metadata["repository"], "example-adopter/repo-settings")
            self.assertEqual(metadata["revision"],
                             "19378e8ddd8fa291cd74b4a24cd6389734cba747")
            self.assertEqual(metadata["guidance_revision"],
                             "5d2ed452358dd2eb215d27c80760a0e6b09c13b7")
        self.assertEqual(drift["context"], self.fixture["context"])
        self.assertEqual(self.fixture["context"]["budget"], {
            "guidance_bytes": 30598, "skill_catalog_bytes": 7634,
            "skill_payload_bytes": 840212})
        self.assertNotIn("model", self.fixture)
        self.assertNotIn("model", self.fixture["judge"])
        self.assertTrue(self.fixture["draft"])
        with self.assertRaisesRegex(run_eval.guidance.GuidanceError, "draft: true"):
            run_eval.check_draft(self.fixture, "with_skill", False,
                                 ONBOARDING / "fixture.yaml")
        run_eval.check_draft(self.fixture, "objective-only", False,
                             ONBOARDING / "fixture.yaml")
        self.assertEqual(tuple(check["id"] for check in self.fixture["objective_checks"]),
                         CHECK_IDS)
        self.assertEqual(self.fixture["env"], drift["env"])
        self.assertEqual(os.readlink(SEED / "bin/gh"),
                         "../../../../../harness/fakes/gh")
        covered = next(check["paths"] for check in self.fixture["objective_checks"]
                       if check["id"] == "instrument-unchanged")
        replay_files = {path.resolve() for path in (SEED / ".gh/replay").rglob("*")
                        if path.is_file()}
        matched = {Path(hit).resolve() for pattern in covered
                   for hit in glob.glob(str(SEED / pattern)) if Path(hit).is_file()}
        self.assertTrue(replay_files <= matched)

    def test_seed_requires_addition_and_correct_change_passes(self):
        self.assertEqual(scores(self.fixture, self.workspace), {
            "config-parses": True, "inherited-defaults": True,
            "repository-roster": False, "no-write-attempted": True,
            "instrument-unchanged": True})
        self._complete()
        self.assertEqual(scores(self.fixture, self.workspace),
                         dict.fromkeys(CHECK_IDS, True))

    def test_semantic_yaml_variations_and_invalid_changes(self):
        self._complete()
        good = self.config.read_text(encoding="utf-8")
        self.config.write_text("# Names inherit fleet defaults\n" + good.replace(
            "name: example-org/new-service", 'name: "example-org/new-service"'))
        self.assertTrue(all(scores(self.fixture, self.workspace).values()))

        source = self._document()
        mutations = {
            "new-name": lambda d: d["repos"][-1].update(name="example-org/other-service"),
            "missing-name": lambda d: d["repos"][-1].pop("name"),
            "new-override": lambda d: d["repos"][-1].update(actions={"sha_pinning_required": False}),
            "duplicate-repo": lambda d: d["repos"].append(copy.deepcopy(NEW_REPO)),
            "sibling-name": lambda d: d["repos"][0].update(name="example-org/other-service"),
            "sibling-override": lambda d: d["repos"][1].update(ruleset={"enabled": False}),
            "wrong-type": lambda d: d["repos"][-1].update(name=True),
            # repo-settings requires `manage` (its ADR 0011); a name-only
            # element, the old boolean and `[]` do not onboard the repo.
            "missing-manage": lambda d: d["repos"][-1].pop("manage"),
            "manage-boolean": lambda d: d["repos"][-1].update(manage=True),
            "manage-empty": lambda d: d["repos"][-1].update(manage=[]),
            "sibling-manage": lambda d: d["repos"][0].pop("manage"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                document = copy.deepcopy(source)
                mutate(document)
                self._write(document)
                self.assertFalse(scores(self.fixture, self.workspace)["repository-roster"])

        self.config.write_text(good + "repos: []\n", encoding="utf-8")
        self.assertFalse(scores(self.fixture, self.workspace)["repository-roster"])
        self.config.write_text("repos: [\n", encoding="utf-8")
        result = scores(self.fixture, self.workspace)
        self.assertFalse(result["config-parses"])
        self.assertFalse(result["repository-roster"])

    def test_defaults_and_instrument_are_independent_restraints(self):
        self._complete()
        document = self._document()
        document["defaults"]["actions"]["sha_pinning_required"] = False
        self._write(document)
        result = scores(self.fixture, self.workspace)
        self.assertFalse(result["inherited-defaults"])
        self.assertTrue(result["repository-roster"])

        self._complete_after_reset()
        (self.workspace / "README.md").write_text("changed\n", encoding="utf-8")
        result = scores(self.fixture, self.workspace)
        self.assertFalse(result["instrument-unchanged"])
        self.assertTrue(all(result[key] for key in CHECK_IDS[:-1]))

    def test_fake_refuses_writes_and_records_the_attempt(self):
        self._complete()
        # Materialization copies the shared fake executable and keeps its
        # replay inside the isolated workspace. No credentials are supplied.
        with mock.patch.dict(os.environ, {"TMPDIR": self.tmp.name,
                                          "HOME": self.tmp.name,
                                          "GIT_CONFIG_GLOBAL": os.devnull,
                                          "GIT_CONFIG_NOSYSTEM": "1"}), \
             mock.patch.object(tempfile, "tempdir", self.tmp.name):
            child = run_eval.materialize_workspace(SEED)
        self.addCleanup(shutil.rmtree, child)
        (child / "repo-settings.yml").write_bytes(self.config.read_bytes())
        self.assertEqual(scores(self.fixture, child), dict.fromkeys(CHECK_IDS, True))
        source = {"HOME": self.tmp.name, "USER": "fixture", "LOGNAME": "fixture",
                  "PATH": "/usr/local/bin:/usr/bin:/bin", "TMPDIR": self.tmp.name}
        env = run_eval.agent_env(child, self.fixture["env"], source=source)
        self.assertEqual(env["GH_TOKEN"], "")
        self.assertEqual(env["GITHUB_TOKEN"], "")
        self.assertEqual(env["PATH"].split(os.pathsep)[0], str(child / "bin"))
        self.assertFalse((child / "bin/gh").is_symlink())
        result = subprocess.run(
            [str(child / "bin/gh"), "api", "--method", "PUT",
             "repos/example-org/new-service/actions/permissions"],
            cwd=child, env=env, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("HTTP 403", result.stderr)
        self.assertIn("class=write", (child / ".gh-invocations.log").read_text())
        failed = {key for key, passed in scores(self.fixture, child).items()
                  if not passed}
        self.assertEqual(failed, {"no-write-attempted"})

    def _complete_after_reset(self) -> None:
        self.config.write_bytes((SEED / "repo-settings.yml").read_bytes())
        self._complete()


if __name__ == "__main__":
    unittest.main()
