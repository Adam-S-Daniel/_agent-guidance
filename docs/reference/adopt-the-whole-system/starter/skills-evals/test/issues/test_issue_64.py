#!/usr/bin/env python3
"""Issue #64 (local half) — `scripts/eval_coverage.py`, the coverage census.

Every test drives the production entry point (`python3 scripts/eval_coverage.py`
as a subprocess) against a synthetic world built under its own mkdtemp: four
registries named and laid out as `harness/registries.yml` says, an `evals/`
tree, and a non-coverage file. Nothing reads the operator's real registry
checkouts or `~/.claude`; no network, no clock.

The committed `evals/non-coverage.yml` and `evals/` tree are only LOADED
(against empty-ish synthetic registries), to prove they still parse; their
contents are not asserted skill by skill.

Discovered and run by test/run_tests.py (see `build_suite`/`DISCOVERY_DIR`
there); also runnable on its own with `python3 test/issues/test_issue_64.py`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

TEST_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = TEST_DIR.parent
SCRIPT = REPO_ROOT / "scripts" / "eval_coverage.py"
REGISTRIES_YML = REPO_ROOT / "harness" / "registries.yml"
COMMITTED_NON_COVERAGE = REPO_ROOT / "evals" / "non-coverage.yml"
EXAMPLE_NON_COVERAGE = REPO_ROOT / "test" / "fixtures" / "evals" / "non-coverage.yml"
PROPAGATION_YML = REPO_ROOT / ".github" / "workflows" / "propagation.yml"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
PUBLISH_TIME = "2026-10-08T05:41:00Z"

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import eval_coverage  # noqa: E402

sys.path.insert(0, str(TEST_DIR))
from lockfile_pins import lock_problem  # noqa: E402

REGISTRIES = {e["name"]: e for e in yaml.safe_load(
    REGISTRIES_YML.read_text(encoding="utf-8"))["registries"]}
PRIVATE = "acsi-agentskills-private"
PRIVATE_COVERED = "zz-private-covered-marker"
PRIVATE_GAP = "zz-private-gap-marker"

# Words the fleet's gitleaks generic rule keys on; none may be a field name in
# the committed non-coverage file.
SCANNER_WORDS = ("access", "auth", "api", "credential", "creds", "key",
                 "passwd", "password", "secret", "token")


class World:
    """A synthetic census input set under a scratch directory."""

    def __init__(self, test: unittest.TestCase):
        self.root = Path(tempfile.mkdtemp(prefix="issue64-"))
        test.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.evals = self.root / "evals"
        self.evals.mkdir()
        self.registry_dirs = {}
        for name in REGISTRIES:
            self.registry_dirs[name] = self.root / "reg" / name
            self.registry_dirs[name].mkdir(parents=True)
        self.skips: list[dict] = []
        self.private_skips: list[dict] = []
        self.populate()

    # building blocks ------------------------------------------------------

    def add_skill(self, registry: str, skill: str, bundle: str = "bundle-x"):
        parts = REGISTRIES[registry]["layout"].split("/")
        parts[-2] = skill
        # any remaining wildcard is the bundle segment
        parts = [bundle if p == "*" else p for p in parts]
        path = self.registry_dirs[registry].joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\nname: {skill}\ndescription: stand-in.\n---\n",
                        encoding="utf-8")

    def link_private_skill(self, level: str, relative: bool) -> Path:
        public = self.registry_dirs["acsi-agentskills"] / "plugins" / "plug-leak"
        private = self.registry_dirs[PRIVATE] / "plugins" / "plug-p"
        suffix = {"plugin": (), "skills": ("skills",),
                  "skill": ("skills", PRIVATE_GAP),
                  "file": ("skills", PRIVATE_GAP, "SKILL.md")}[level]
        link = public.joinpath(*suffix)
        target = private.joinpath(*suffix)
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(os.path.relpath(target, link.parent) if relative else target,
                        target_is_directory=level != "file")
        return link

    def add_fixture(self, relpath: str, skill: str | None, registry: str | None,
                    extra: dict | None = None):
        doc = dict(extra or {})
        if skill is not None:
            doc["skill"] = skill
        if registry is not None:
            doc["registry"] = REGISTRIES[registry]["url"]
        path = self.evals / relpath / "fixture.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(doc), encoding="utf-8")

    def skip(self, registry: str, skill: str, reason: str = "because",
             private: bool = False):
        (self.private_skips if private else self.skips).append(
            {"registry": registry, "skill": skill, "decision": "skip",
             "reason": reason})

    def populate(self):
        # three layouts: plugin bundles, flat, dot-dir
        for skill in ("alpha", "beta", "gamma", "delta"):
            self.add_skill("acsi-agentskills", skill, "plug-a")
        self.add_skill("acsi-agentskills", "epsilon", "plug-b")
        self.add_skill("acsi-agentskills", "zeta", "plug-b")
        self.add_skill("acsi-agentskills", "eta", "plug-b")
        self.add_skill(PRIVATE, PRIVATE_COVERED, "plug-p")
        self.add_skill(PRIVATE, PRIVATE_GAP, "plug-p")
        self.add_fixture("alpha", "alpha", "acsi-agentskills")          # flat
        self.add_fixture("beta/one", "beta", "acsi-agentskills")        # nested
        self.add_fixture("beta/two", "beta", "acsi-agentskills")
        self.add_fixture("zeta", "zeta", "acsi-agentskills")
        self.add_fixture(PRIVATE_COVERED, PRIVATE_COVERED, PRIVATE)
        # a non-skill subject: ignored, not refused
        self.add_fixture("guidance/_delivery", None, None,
                         {"subject": "guidance"})
        self.skip("acsi-agentskills", "gamma", "machine-bound | WSL")

    def clear_all_gaps(self):
        self.skip("acsi-agentskills", "delta")
        self.skip("acsi-agentskills", "epsilon")
        self.skip("acsi-agentskills", "eta")
        self.skip(PRIVATE, PRIVATE_GAP, private=True)

    @staticmethod
    def write_raw(path: Path, content: str | bytes) -> None:
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")

    def raw_fixture(self, relpath: str, content: str | bytes) -> None:
        path = self.evals / relpath / "fixture.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.write_raw(path, content)

    # running --------------------------------------------------------------

    def _write_skips(self, name: str, rows: list[dict]) -> Path:
        path = self.root / name
        path.write_text(yaml.safe_dump({"skips": rows}), encoding="utf-8")
        return path

    def run(self, *extra: str, omit: tuple[str, ...] = (),
            paths: dict | None = None, order: list[str] | None = None,
            use_private_file: bool = True,
            raw_non_coverage: str | bytes | None = None,
            raw_private: str | bytes | None = None):
        non_coverage = self._write_skips("non-coverage.yml", self.skips)
        if raw_non_coverage is not None:
            self.write_raw(non_coverage, raw_non_coverage)
        argv = [sys.executable, str(SCRIPT), "--evals-dir", str(self.evals),
                "--non-coverage", str(non_coverage)]
        if raw_private is not None:
            private = self.root / "private-non-coverage.yml"
            self.write_raw(private, raw_private)
            argv += ["--private-non-coverage", str(private)]
        elif self.private_skips and use_private_file:
            argv += ["--private-non-coverage",
                     str(self._write_skips("private-non-coverage.yml",
                                           self.private_skips))]
        paths = {**self.registry_dirs, **(paths or {})}
        for name in (order or list(REGISTRIES)):
            if name not in omit:
                argv += ["--registry", f"{name}={paths[name]}"]
        argv += list(extra)
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.root),
               "LC_ALL": "C.UTF-8"}
        return subprocess.run(argv, capture_output=True, text=True, env=env,
                              cwd=self.root, timeout=120)

    def json(self, *extra: str, **kw) -> dict:
        proc = self.run("--json", *extra, **kw)
        assert proc.returncode in (0, 1), proc.stderr
        return json.loads(proc.stdout)


def rows(doc: dict, registry: str) -> dict:
    reg = next(r for r in doc["registries"] if r["name"] == registry)
    return {r["skill"]: r for r in reg["skills"]}


class StatusesAndCounts(unittest.TestCase):
    def test_draft_fixture_does_not_close_a_gap(self):
        world = World(self)
        world.add_fixture("pending/delta", "delta", "acsi-agentskills",
                          {"draft": True, "context": {
                              "repository": "example-adopter/skills-evals",
                              "revision": "a" * 40,
                              "guidance_revision": "b" * 40,
                              "budget": {"guidance_bytes": 1,
                                         "skill_catalog_bytes": 1,
                                         "skill_payload_bytes": 1}}})
        doc = world.json()
        self.assertEqual(rows(doc, "acsi-agentskills")["delta"]["status"], "gap")
        self.assertEqual(rows(doc, "acsi-agentskills")["delta"]["fixtures"], 0)

    def test_every_status_fixture_count_and_reason(self):
        doc = World(self).json()
        a = rows(doc, "acsi-agentskills")
        self.assertEqual(
            {k: (v["status"], v["fixtures"], v["bundle"]) for k, v in a.items()},
            {"alpha": ("covered", 1, "plug-a"),
             "beta": ("covered", 2, "plug-a"),       # nested fixtures count
             "gamma": ("skipped", 0, "plug-a"),
             "delta": ("gap", 0, "plug-a"),
             "epsilon": ("gap", 0, "plug-b"),
             "zeta": ("covered", 1, "plug-b"),
             "eta": ("gap", 0, "plug-b")})
        self.assertEqual(a["gamma"]["reason"], "machine-bound | WSL")
        self.assertIsNone(a["alpha"]["reason"])
        counts = {r["name"]: r["counts"] for r in doc["registries"]}
        self.assertEqual(counts["acsi-agentskills"],
                         {"total": 7, "covered": 3, "skipped": 1, "gap": 3})
        self.assertEqual(doc["totals"],
                         {"total": 9, "covered": 4, "skipped": 1, "gap": 4})
        self.assertEqual(doc["problems"], [])

    def test_a_fixture_covers_by_its_own_fields_not_its_directory_name(self):
        w = World(self)
        # same skill name in a different registry must not cover eta's
        # namesake, and a misleadingly named directory still covers `delta`
        w.add_fixture("misc/anything", "delta", "acsi-agentskills")
        w.add_fixture("eta-elsewhere", "eta", PRIVATE)  # wrong registry
        doc = w.json()
        self.assertEqual(rows(doc, "acsi-agentskills")["delta"]["fixtures"], 1)
        self.assertEqual(rows(doc, "acsi-agentskills")["eta"]["status"], "gap")
        self.assertIn("orphan_fixture", [p["kind"] for p in doc["problems"]])

    def test_markdown_table_has_status_counts_and_escapes_the_reason(self):
        proc = World(self).run()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        md = proc.stdout
        self.assertIn("| acsi-agentskills | 7 | 3 | 1 | 3 |", md)
        self.assertIn("| **Total** | 9 | 4 | 1 | 4 |", md)
        self.assertIn("| plug-a | gamma | skipped | 0 | machine-bound \\| WSL |", md)
        self.assertIn("| plug-a | delta | GAP | 0 |  |", md)
        self.assertIn("| plug-a | beta | covered | 2 |", md)

    def test_output_is_deterministic_and_has_no_local_paths(self):
        w = World(self)
        first = w.run("--json")
        second = w.run("--json", order=list(reversed(list(REGISTRIES))))
        self.assertEqual(first.stdout, second.stdout)
        self.assertNotIn(str(w.root), first.stdout + w.run().stdout)


class ExitCodes(unittest.TestCase):
    def test_a_gap_is_reported_but_only_fails_under_check(self):
        w = World(self)
        self.assertEqual(w.run().returncode, 0)
        proc = w.run("--check")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("delta", proc.stdout)  # the table still printed

    def test_check_passes_when_every_gap_is_covered_or_explicitly_skipped(self):
        w = World(self)
        w.clear_all_gaps()
        proc = w.run("--check")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(w.json()["totals"]["gap"], 0)


class Refusals(unittest.TestCase):
    def assert_refused(self, proc, needle: str):
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertEqual(proc.stdout, "", "a refusal must not print a census")
        self.assertIn(needle, proc.stderr)

    def test_a_missing_registry_path_is_refused_not_counted_as_zero(self):
        w = World(self)
        proc = w.run("--check", paths={"acsi-agentskills": w.root / "nope"})
        self.assert_refused(proc, "does not resolve to a directory")
        self.assertIn("acsi-agentskills", proc.stderr)

    def test_a_registry_with_no_checkout_given_is_refused(self):
        # no fallback to a sibling directory
        self.assert_refused(World(self).run(omit=("acsi-agentskills",)),
                            "has no --registry acsi-agentskills=PATH")

    def test_a_registry_that_yields_no_skills_is_refused(self):
        w = World(self)
        shutil.rmtree(w.registry_dirs["acsi-agentskills"])
        w.registry_dirs["acsi-agentskills"].mkdir()
        self.assert_refused(w.run("--check"), "yields no skills")

    def test_an_unknown_registry_name_is_refused(self):
        w = World(self)
        proc = w.run("--registry", f"example_site_platform={w.root}")
        self.assert_refused(proc, "example_site_platform")

    def test_a_skill_fixture_without_a_known_registry_is_refused(self):
        w = World(self)
        w.add_fixture("noreg", "alpha", None)
        self.assert_refused(w.run(), "noreg")
        w2 = World(self)
        path = w2.evals / "badurl" / "fixture.yaml"
        path.parent.mkdir()
        path.write_text("skill: alpha\nregistry: https://example.com/x/y\n",
                        encoding="utf-8")
        self.assert_refused(w2.run(), "badurl")

    def test_a_skill_name_in_two_bundles_is_refused_in_every_mode(self):
        w = World(self)
        w.clear_all_gaps()
        w.add_skill("acsi-agentskills", "alpha", "plug-b")
        for flags in ((), ("--json",), ("--check",), ("--json", "--check")):
            with self.subTest(flags=flags):
                self.assert_refused(w.run(*flags), "more than one bundle")
        self.assertIn("'alpha'", w.run().stderr)  # public: the name helps

    def test_invalid_utf8_in_any_input_file_is_refused_naming_the_file(self):
        bad = b"skips: []\n# \xff\xfe\n"
        w = World(self)
        self.assert_refused(w.run(raw_non_coverage=bad), "not valid UTF-8")
        self.assertIn("non-coverage.yml", w.run(raw_non_coverage=bad).stderr)
        w2 = World(self)
        w2.raw_fixture("badbytes", b"skill: alpha\n\xff\xfe\n")
        proc = w2.run()
        self.assert_refused(proc, "not valid UTF-8")
        self.assertIn("fixture.yaml", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        w3 = World(self)
        self.assert_refused(w3.run(raw_private=bad), "not valid UTF-8")

    def test_a_fixture_at_any_depth_counts_like_eval_ymls_find(self):
        # eval.yml: `find evals -mindepth 1 -name fixture.yaml`, any depth
        w = World(self)
        w.add_fixture("deep/er/still", "delta", "acsi-agentskills")
        w.add_fixture("deep/er/still/and/deeper", "delta", "acsi-agentskills")
        doc = w.json()
        self.assertEqual(rows(doc, "acsi-agentskills")["delta"]["fixtures"], 2)
        self.assertEqual(rows(doc, "acsi-agentskills")["delta"]["status"], "covered")
        self.assertEqual(doc["totals"]["covered"], 5)

    def test_a_fixture_with_a_null_or_blank_skill_is_refused(self):
        for body in ("skill:\nregistry: https://github.com/example-adopter/"
                     "acsi-agentskills\n",
                     "skill: ~\n", "skill: ''\n", "skill: '   '\n",
                     "skill: [alpha]\n"):
            with self.subTest(body=body):
                w = World(self)
                w.raw_fixture("nullskill", body)
                self.assert_refused(w.run(), "nullskill")
                self.assertIn("'skill:'", w.run().stderr)
        # the key ABSENT is still a non-skill subject, ignored
        w = World(self)
        w.raw_fixture("subject", "subject: guidance\n")
        self.assertEqual(w.run().returncode, 0)

    def test_an_evals_dir_with_no_fixture_is_refused(self):
        w = World(self)
        shutil.rmtree(w.evals)
        w.evals.mkdir()
        self.assert_refused(w.run(), "no fixture.yaml")
        (w.evals / "notes.txt").write_text("not a fixture", encoding="utf-8")
        self.assert_refused(w.run("--check"), "no fixture.yaml")

    def test_a_malformed_non_coverage_file_is_refused(self):
        for bad, needle in (
            ("skips: []\nextra: 1\n", "exactly one key"),
            ("- not a mapping\n", "exactly one key"),
            ("skips:\n  - {registry: acsi-agentskills, skill: eta, "
             "decision: skip, reasn: typo}\n", "exactly the fields"),
            ("skips:\n  - {registry: acsi-agentskills, skill: eta, "
             "decision: skip, reason: ''}\n", "non-blank"),
            ("skips:\n  - {registry: nowhere, skill: x, decision: skip, "
             "reason: r}\n", "not in harness/registries.yml"),
            ("skips:\n  - {registry: acsi-agentskills, skill: eta, "
             "decision: skip, reason: r}\n  - {registry: acsi-agentskills, "
             "skill: eta, decision: skip, reason: r}\n", "second row"),
        ):
            with self.subTest(bad=bad):
                self.assert_refused(World(self).run(raw_non_coverage=bad), needle)


class Problems(unittest.TestCase):
    def kinds(self, doc: dict) -> set:
        return {(p["kind"], p.get("skill")) for p in doc["problems"]}

    def test_a_stale_skip_is_reported_and_fails_check(self):
        w = World(self)
        w.clear_all_gaps()
        w.skip("acsi-agentskills", "renamed-away")
        self.assertEqual(w.json()["problems"],
                         [{"kind": "stale_skip", "registry": "acsi-agentskills",
                           "skill": "renamed-away"}])
        self.assertEqual(w.run("--check").returncode, 1)

    def test_a_skip_for_a_skill_that_also_has_a_fixture_is_reported(self):
        w = World(self)
        w.clear_all_gaps()
        w.skip("acsi-agentskills", "alpha")
        self.assertEqual(self.kinds(w.json()), {("skip_but_covered", "alpha")})
        self.assertEqual(w.run("--check").returncode, 1)

    def test_a_fixture_for_a_vanished_skill_is_reported(self):
        w = World(self)
        w.clear_all_gaps()
        w.add_fixture("old", "renamed-away", "acsi-agentskills")
        self.assertEqual(self.kinds(w.json()), {("orphan_fixture", "renamed-away")})
        self.assertEqual(w.run("--check").returncode, 1)



class PrivateNames(unittest.TestCase):
    def test_counts_only_by_default_in_both_formats(self):
        w = World(self)
        for flags in ((), ("--json",), ("--check",), ("--json", "--check")):
            with self.subTest(flags=flags):
                proc = w.run(*flags)
                self.assertIn(proc.returncode, (0, 1))
                self.assertNotIn("zz-private", proc.stdout + proc.stderr)
        doc = w.json()
        reg = next(r for r in doc["registries"] if r["name"] == PRIVATE)
        self.assertEqual((reg["names_withheld"], reg["skills"]), (True, []))
        self.assertEqual(reg["counts"],
                         {"total": 2, "covered": 1, "skipped": 0, "gap": 1})

    def test_names_appear_only_with_the_flag(self):
        w = World(self)
        doc = w.json("--include-private-names")
        self.assertEqual(rows(doc, PRIVATE)[PRIVATE_COVERED]["status"], "covered")
        self.assertEqual(rows(doc, PRIVATE)[PRIVATE_GAP]["status"], "gap")
        self.assertIn(PRIVATE_GAP, w.run("--include-private-names").stdout)

    def test_a_problem_in_the_private_registry_does_not_name_the_skill(self):
        w = World(self)
        w.skip(PRIVATE, "zz-private-renamed", private=True)
        w.add_fixture("old-private", "zz-private-vanished", PRIVATE)
        proc = w.run("--json")
        self.assertNotIn("zz-private-renamed", proc.stdout)
        self.assertNotIn("zz-private-vanished", proc.stdout)
        kinds = sorted(p["kind"] for p in json.loads(proc.stdout)["problems"])
        self.assertEqual(kinds, ["orphan_fixture", "stale_skip"])
        self.assertIn("zz-private-renamed",
                      w.run("--json", "--include-private-names").stdout)

    def test_no_refusal_path_prints_a_private_skill_name(self):
        marker = "zz-private-leak-marker"
        public_url = "https://github.com/example-adopter/acsi-agentskills"
        row = ("skips:\n  - {registry: %s, skill: %s, decision: skip, "
               "reason: r}\n")

        def duplicate(w):
            w.add_skill(PRIVATE, PRIVATE_COVERED, "plug-q")

        cases = {
            # fixture problems where the registry is unknown or missing
            "fixture, no registry": (lambda w: w.raw_fixture(
                "leaky/a", f"skill: {marker}\n"), "no 'registry:'"),
            "fixture, unknown registry": (lambda w: w.raw_fixture(
                "leaky/b", f"skill: {marker}\nregistry: https://example.com/x/y\n"),
                "unknown registry"),
            "fixture, invalid name": (lambda w: w.raw_fixture(
                "leaky/c", f"skill: '{marker}/..'\nregistry: {public_url}\n"),
                "not a valid skill name"),
            "fixture, broken YAML": (lambda w: w.raw_fixture(
                "leaky/d", f"skill: {marker}\nbad: [unclosed\n"),
                "not valid YAML"),
            "fixture, unknown YAML tag": (lambda w: w.raw_fixture(
                "leaky/e", f"skill: !{marker} x\n"), "not valid YAML"),
            # a private registry's own structural refusals
            "private duplicate": (duplicate, "more than one bundle"),
        }
        for label, (mutate, needle) in cases.items():
            with self.subTest(label):
                w = World(self)
                mutate(w)
                proc = w.run()
                self.assertEqual((proc.returncode, proc.stdout), (2, ""),
                                 proc.stderr)
                self.assertIn(needle, proc.stderr)
                self.assertNotIn(marker, proc.stderr)
                self.assertNotIn(PRIVATE_COVERED, proc.stderr)
        # the operator-held private file, every way it can be refused
        one = f"  - {{registry: {PRIVATE}, skill: {marker}, decision: skip, reason: r}}\n"
        for label, raw, needle in (
            ("duplicate row", "skips:\n" + one * 2, "second row"),
            ("invalid name", row % (PRIVATE, f'"{marker}/.."'),
             "not a valid skill name"),
            ("extra field", f"skips:\n  - {{registry: {PRIVATE}, skill: "
             f"{marker}, decision: skip, reason: r, x: 1}}\n",
             "exactly the fields"),
            ("broken YAML", f"skips: [{marker}: : :\n", "not valid YAML"),
            # PyYAML's own message quotes an unknown tag from the file
            ("unknown YAML tag", f"skips: !{marker} x\n", "not valid YAML"),
            ("public registry row", row % ("acsi-agentskills", marker),
             "private registries only"),
        ):
            with self.subTest(f"private file, {label}"):
                w = World(self)
                proc = w.run(raw_private=raw)
                self.assertEqual((proc.returncode, proc.stdout), (2, ""),
                                 proc.stderr)
                self.assertIn(needle, proc.stderr)
                self.assertNotIn(marker, proc.stderr)
        # and the same row in the PUBLIC file
        w = World(self)
        proc = w.run(raw_non_coverage=row % (PRIVATE, marker))
        self.assertEqual((proc.returncode, proc.stdout), (2, ""))
        self.assertNotIn(marker, proc.stderr)

    def test_the_committed_file_refuses_private_rows(self):
        w = World(self)
        w.skip(PRIVATE, PRIVATE_GAP)  # in the PUBLIC file
        proc = w.run()
        self.assertEqual((proc.returncode, proc.stdout), (2, ""))
        self.assertIn("may not be committed", proc.stderr)
        self.assertNotIn(PRIVATE_GAP, proc.stderr)

    def test_the_operator_held_file_takes_private_rows_only(self):
        w = World(self)
        w.private_skips.append({"registry": "acsi-agentskills", "skill": "eta",
                                "decision": "skip", "reason": "r"})
        proc = w.run()
        self.assertEqual((proc.returncode, proc.stdout), (2, ""))
        self.assertIn("private registries only", proc.stderr)

    def test_a_private_skip_from_the_operator_file_clears_the_gap(self):
        w = World(self)
        w.clear_all_gaps()
        self.assertEqual(w.run("--check").returncode, 0)
        w.private_skips.clear()
        self.assertEqual(w.run("--check").returncode, 1)  # same census, no file


class RepoFiles(unittest.TestCase):
    def test_every_private_looking_registry_is_in_the_allowlist(self):
        private_named = {n for n in REGISTRIES if "private" in n}
        self.assertTrue(private_named)
        self.assertLessEqual(private_named, eval_coverage.PRIVATE_REGISTRIES)
        self.assertLessEqual(eval_coverage.PRIVATE_REGISTRIES, set(REGISTRIES))

    def test_the_committed_non_coverage_file_is_public_safe_and_well_formed(self):
        doc = yaml.safe_load(COMMITTED_NON_COVERAGE.read_text(encoding="utf-8"))
        self.assertEqual(list(doc), ["skips"])
        # The kit commits the list empty (DECISIONS X9); the invented example
        # rows the scratch checkout carries are held to the same rules.
        example = yaml.safe_load(EXAMPLE_NON_COVERAGE.read_text(encoding="utf-8"))
        self.assertTrue(example["skips"])
        doc = {"skips": doc["skips"] + example["skips"]}
        for row in doc["skips"]:
            self.assertEqual(set(row), set(eval_coverage.SKIP_FIELDS))
            self.assertIn(row["registry"], REGISTRIES)
            self.assertNotIn(row["registry"], eval_coverage.PRIVATE_REGISTRIES)
            for field in row:
                self.assertFalse(
                    any(w in field.lower() for w in SCANNER_WORDS), field)
        pairs = [(r["registry"], r["skill"]) for r in doc["skips"]]
        self.assertEqual(len(pairs), len(set(pairs)))


    def test_the_committed_evals_tree_and_skip_file_load_through_the_script(self):
        w = World(self)
        non_coverage = w.root / "committed.yml"
        shutil.copy(COMMITTED_NON_COVERAGE, non_coverage)
        argv = [sys.executable, str(SCRIPT), "--json",
                "--evals-dir", str(REPO_ROOT / "evals"),
                "--non-coverage", str(non_coverage)]
        for name, path in w.registry_dirs.items():
            argv += ["--registry", f"{name}={path}"]
        proc = subprocess.run(
            argv, capture_output=True, text=True, cwd=w.root, timeout=120,
            env={"PATH": os.environ.get("PATH", ""), "HOME": str(w.root)})
        # synthetic registries lack the real skills, so problems are expected;
        # what is asserted is that nothing was REFUSED.
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["schema"], 1)

    def test_the_script_imports_no_network_or_subprocess_module(self):
        tree = ast_imports(SCRIPT)
        self.assertFalse(tree & {"socket", "urllib", "http", "requests",
                                 "subprocess", "shutil"}, tree)


class Publication(unittest.TestCase):
    def publish(self, world: World, *extra: str, **kwargs):
        directory = world.root / "publication"
        proc = world.run("--publish-dir", str(directory), "--timestamp",
                         PUBLISH_TIME, *extra, **kwargs)
        report = json.loads((directory / "latest.json").read_text(encoding="utf-8"))
        return proc, report, directory

    def test_complete_report_badge_and_metadata_are_distinct_from_delivery(self):
        w = World(self)
        # A draft fixture is a generated review request (issue #60), so it is
        # readiness metadata only: `delta` stays a gap, not covered.
        w.raw_fixture("declared", "skill: delta\nregistry: https://github.com/"
                      "example-adopter/acsi-agentskills\ndraft: true\ncontext:\n"
                      "  repository: example-adopter/skills-evals\n"
                      "  revision: " + "a" * 40 + "\n"
                      "  guidance_revision: null\n  budget:\n"
                      "    guidance_bytes: 1\n    skill_catalog_bytes: 1\n"
                      "    skill_payload_bytes: 1\n")
        proc, report, directory = self.publish(w)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["totals"],
                         {"total": 9, "covered": 4, "skipped": 1, "gap": 4})
        self.assertEqual(report["fixture_readiness"],
                         {"total": 7, "draft": 1, "context_declared": 1,
                          "context_missing": 6, "guidance_unproven": 1,
                          "paired_context_delivery_verified": False})
        badge = json.loads((directory / "badge.json").read_text(encoding="utf-8"))
        self.assertEqual(badge["schemaVersion"], 1)
        self.assertEqual(badge["message"], "4 covered · 1 skipped · 4 gap · 2026-10-08")
        self.assertEqual(badge["color"], "orange")
        self.assertIn("Paired context delivery verified: no",
                      (directory / "latest.md").read_text(encoding="utf-8"))

    def test_missing_or_empty_registry_is_explicitly_unresolved_without_totals(self):
        for empty in (False, True):
            with self.subTest(empty=empty):
                w = World(self)
                if empty:
                    shutil.rmtree(w.registry_dirs["acsi-agentskills"])
                    w.registry_dirs["acsi-agentskills"].mkdir()
                    kwargs = {}
                else:
                    kwargs = {"paths": {"acsi-agentskills": w.root / "missing"}}
                proc, report, directory = self.publish(w, **kwargs)
                self.assertEqual(proc.returncode, 2)
                self.assertEqual(report["status"], "unresolved")
                self.assertIsNone(report["totals"])
                self.assertIn("acsi-agentskills", report["unresolved"])
                self.assertEqual(next(r for r in report["registries"]
                                      if r["name"] == "acsi-agentskills")["status"],
                                 "unresolved")
                self.assertFalse((directory / "badge.json").exists())
                self.assertNotIn("**Total**", (directory / "latest.md").read_text())

    def test_unresolved_overwrites_an_old_complete_report_and_removes_badge(self):
        w = World(self)
        first, _, directory = self.publish(w)
        self.assertEqual(first.returncode, 0)
        self.assertTrue((directory / "badge.json").exists())
        second, report, _ = self.publish(
            w, paths={PRIVATE: w.root / "missing-private"})
        self.assertEqual(second.returncode, 2)
        self.assertEqual(report["status"], "unresolved")
        self.assertIsNone(report["totals"])
        self.assertFalse((directory / "badge.json").exists())

    def test_gap_is_publishable_but_check_still_returns_one(self):
        w = World(self)
        default_proc, default_report, _ = self.publish(w)
        self.assertEqual(default_proc.returncode, 0, default_proc.stderr)
        self.assertEqual(default_report["status"], "complete")
        proc, report, directory = self.publish(w, "--check")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(report["status"], "complete")
        self.assertTrue((directory / "badge.json").is_file())
        self.assertEqual(w.run("--check").returncode, 1)

    def test_badge_green_requires_no_gap_and_no_problem(self):
        w = World(self)
        w.clear_all_gaps()
        proc, report, directory = self.publish(w)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(report["totals"]["gap"], 0)
        self.assertEqual(json.loads((directory / "badge.json").read_text())["color"],
                         "green")
        w.skip("acsi-agentskills", "renamed-away")
        proc, report, directory = self.publish(w)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(report["totals"]["gap"], 0)
        self.assertEqual(report["problems"][0]["kind"], "stale_skip")
        self.assertEqual(json.loads((directory / "badge.json").read_text())["color"],
                         "orange")

    def test_private_content_and_paths_never_enter_publication(self):
        w = World(self)
        w.skip(PRIVATE, PRIVATE_GAP, "zz-private-reason-marker", private=True)
        w.add_fixture("zz-private-path-marker", "zz-private-vanished-marker", PRIVATE)
        proc, _, directory = self.publish(w)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        output = "".join(p.read_text(encoding="utf-8") for p in directory.iterdir())
        for marker in (PRIVATE_COVERED, PRIVATE_GAP, "plug-p", "zz-private-reason-marker",
                       "zz-private-path-marker", "zz-private-vanished-marker",
                       str(w.root)):
            self.assertNotIn(marker, output + proc.stderr)
        refused = w.run("--publish-dir", str(directory), "--timestamp",
                        PUBLISH_TIME, "--include-private-names")
        self.assertEqual(refused.returncode, 2)
        self.assertNotIn(PRIVATE_GAP, refused.stdout + refused.stderr)

    def test_registry_symlink_escapes_refuse_without_private_names_or_paths(self):
        for level in ("plugin", "skills", "skill", "file"):
            for relative in (False, True):
                with self.subTest(level=level, relative=relative):
                    w = World(self)
                    w.link_private_skill(level, relative)
                    local = w.run("--json")
                    self.assertEqual(local.returncode, 2, local.stderr)
                    self.assertEqual(local.stdout, "")
                    self.assertIn("acsi-agentskills", local.stderr)
                    proc, report, directory = self.publish(w)
                    self.assertEqual(proc.returncode, 2, proc.stderr)
                    self.assertEqual(report["status"], "unresolved")
                    self.assertEqual(report["unresolved"], ["acsi-agentskills"])
                    self.assertIsNone(report["totals"])
                    self.assertFalse((directory / "badge.json").exists())
                    content = local.stderr + proc.stdout + proc.stderr + "".join(
                        p.read_text(encoding="utf-8") for p in directory.iterdir())
                    for marker in (PRIVATE_COVERED, PRIVATE_GAP, "plug-p",
                                   "plug-leak", str(w.root)):
                        self.assertNotIn(marker, content)

    def test_broken_or_cyclic_registry_symlinks_are_unresolved(self):
        for level in ("plugin", "skills", "skill", "file"):
            for cyclic in (False, True):
                with self.subTest(level=level, cyclic=cyclic):
                    w = World(self)
                    link = w.link_private_skill(level, relative=True)
                    link.unlink()
                    link.symlink_to(link.name if cyclic else "zz-private-missing-marker",
                                    target_is_directory=level != "file")
                    local = w.run("--json")
                    self.assertEqual(local.returncode, 2, local.stderr)
                    self.assertEqual(local.stdout, "")
                    proc, report, directory = self.publish(w)
                    self.assertEqual(proc.returncode, 2, proc.stderr)
                    self.assertEqual(report["status"], "unresolved")
                    self.assertEqual(report["unresolved"], ["acsi-agentskills"])
                    self.assertIsNone(report["totals"])
                    self.assertFalse((directory / "badge.json").exists())
                    content = local.stderr + proc.stderr + "".join(
                        p.read_text(encoding="utf-8") for p in directory.iterdir())
                    for marker in (PRIVATE_GAP, "zz-private-missing-marker", str(w.root)):
                        self.assertNotIn(marker, content)

    def test_registry_symlinks_within_the_root_preserve_the_census(self):
        suffixes = {"plugin": (), "skills": ("skills",),
                    "skill": ("skills", "alpha"),
                    "file": ("skills", "alpha", "SKILL.md")}
        for level, suffix in suffixes.items():
            with self.subTest(level=level):
                w = World(self)
                registry = w.registry_dirs["acsi-agentskills"]
                link = (registry / "plugins" / "plug-a").joinpath(*suffix)
                target = registry / "linked-content"
                link.rename(target)
                link.symlink_to(os.path.relpath(target, link.parent),
                                target_is_directory=level != "file")
                local = w.run("--json")
                self.assertEqual(local.returncode, 0, local.stderr)
                proc, report, directory = self.publish(w)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(report["status"], "complete")
                self.assertEqual(report["totals"], json.loads(local.stdout)["totals"])
                self.assertEqual(rows(report, "acsi-agentskills")["alpha"]["status"],
                                 "covered")
                self.assertTrue((directory / "badge.json").exists())

    def test_context_duplicate_or_invalid_draft_refuses_without_input_values(self):
        for raw in ("context:\n  repository: example-adopter/skills-evals\n"
                    "  repository: Example/zz-private-marker\n  revision: " + "a" * 40 + "\n"
                    "  guidance_revision: " + "b" * 40 + "\n  budget:\n"
                    "    guidance_bytes: 1\n    skill_catalog_bytes: 1\n"
                    "    skill_payload_bytes: 1\n",
                    "draft: perhaps\n"):
            with self.subTest(raw=raw):
                w = World(self)
                w.raw_fixture("zz-private-path-marker", raw)
                proc, report, directory = self.publish(w)
                self.assertEqual(proc.returncode, 2)
                self.assertEqual(report["status"], "unresolved")
                self.assertIsNone(report["totals"])
                self.assertFalse((directory / "badge.json").exists())
                content = (directory / "latest.json").read_text() + proc.stderr
                self.assertNotIn("zz-private", content)

    def test_registry_markup_cannot_enter_the_summary_as_html_or_a_link(self):
        w = World(self)
        w.add_skill("acsi-agentskills", "safe-name", "[bundle](example.com)")
        w.skip("acsi-agentskills", "safe-name", "<img src=x> [click](https://example.net)")
        proc, report, directory = self.publish(w)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(rows(report, "acsi-agentskills")["safe-name"]["status"], "skipped")
        summary = (directory / "latest.md").read_text(encoding="utf-8")
        self.assertIn("safe-name", summary)
        self.assertNotIn("<img", summary)
        self.assertNotIn("[click](https://example.net)", summary)
        self.assertNotIn("[bundle](example.com)", summary)

    def test_invalid_registry_skill_directory_is_refused(self):
        w = World(self)
        w.add_skill("acsi-agentskills", "bad name")
        proc, report, directory = self.publish(w)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(report["status"], "unresolved")
        self.assertIn("acsi-agentskills", report["unresolved"])
        self.assertFalse((directory / "badge.json").exists())
        self.assertNotIn("bad name", (directory / "latest.md").read_text())

    def test_publication_requires_canonical_utc_timestamp(self):
        w = World(self)
        directory = w.root / "publication"
        for flags in ((), ("--timestamp", "2026-10-08T05:41:00+00:00"),
                      ("--timestamp", "2026-02-30T05:41:00Z"),
                      ("--timestamp", "2026-10-08T5:41:00Z")):
            with self.subTest(flags=flags):
                proc = w.run("--publish-dir", str(directory), *flags)
                self.assertEqual(proc.returncode, 2)
                self.assertEqual(proc.stdout, "")
        self.assertFalse(directory.exists())
        self.assertEqual(w.run("--timestamp", PUBLISH_TIME).returncode, 2)


class PublicationWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = yaml.load(PROPAGATION_YML.read_text(encoding="utf-8"),
                                 Loader=yaml.BaseLoader)

    def test_workflow_does_not_inherit_environment_credentials(self):
        # An empty broad scope prevents credentials under alternative names,
        # including secret references, from reaching checkout or census steps.
        self.assertEqual(self.workflow.get("env", {}), {})

    def test_coverage_jobs_do_not_inherit_environment_credentials(self):
        for name in ("coverage", "coverage-publish"):
            with self.subTest(job=name):
                self.assertEqual(self.workflow["jobs"][name].get("env", {}), {})

    def test_census_is_read_only_and_private_checkout_is_schedule_only(self):
        jobs = self.workflow["jobs"]
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})
        self.assertNotIn("permissions", jobs["coverage"])
        steps = jobs["coverage"]["steps"]
        checkouts = [s for s in steps if s.get("uses", "").startswith("actions/checkout@")]
        self.assertEqual(len(checkouts), 3)
        self.assertTrue(all(s["with"]["persist-credentials"] == "false"
                            for s in checkouts))
        private = next(s for s in checkouts if s["with"].get("repository", "").endswith("-private"))
        mint = next(s for s in steps if s.get("id") == "registry-token")
        self.assertLess(steps.index(mint), steps.index(private))
        # The tag is Dependabot's to move; what stays fixed is the action and
        # that its tag is locked in actions.lock.
        self.assertTrue(mint["uses"].startswith("actions/create-github-app-token@"))
        self.assertIsNone(lock_problem(mint["uses"], PROPAGATION_YML))
        self.assertEqual(mint["if"],
                         "github.event_name == 'schedule' && "
                         "vars.REGISTRY_READ_APP_CLIENT_ID != ''")
        self.assertEqual(mint["continue-on-error"], "true")
        self.assertEqual(mint["with"], {
            "client-id": "${{ vars.REGISTRY_READ_APP_CLIENT_ID }}",
            "private-key": "${{ secrets.REGISTRY_READ_APP_PEM }}",
            "owner": "example-adopter",
            "repositories": "acsi-agentskills-private",
            "permission-contents": "read"})
        self.assertEqual(private["if"],
                         "github.event_name == 'schedule' && "
                         "steps.registry-token.outcome == 'success'")
        self.assertEqual(private["with"]["token"],
                         "${{ steps.registry-token.outputs.token }}")
        self.assertNotIn("COVERAGE_REGISTRY_READ_TOKEN", json.dumps(self.workflow))
        self.assertTrue(all("token" not in s.get("with", {}) for s in checkouts
                            if s is not private))
        self.assertTrue(all(s.get("continue-on-error") == "true"
                            for s in checkouts if s is not checkouts[0]))
        self.assertNotIn("GITHUB_TOKEN", str(jobs["coverage"]))

    def test_publication_is_schedule_only_and_write_auth_is_step_local(self):
        job = self.workflow["jobs"]["coverage-publish"]
        self.assertEqual(job["if"],
                         "github.event_name == 'schedule' && needs.coverage.result == 'success'")
        self.assertEqual(job["needs"], "coverage")
        self.assertEqual(job["permissions"], {"contents": "write"})
        self.assertEqual(job["concurrency"]["group"], "real-eval")
        steps = job["steps"]
        self.assertEqual(steps[0]["with"],
                         {"ref": "${{ github.sha }}", "persist-credentials": "false"})
        self.assertEqual(steps[-1]["env"],
                         {"GITHUB_TOKEN": "${{ secrets.GITHUB_TOKEN }}"})
        self.assertEqual(steps[-1]["if"], "steps.prepare.outputs.changed == 'true'")
        self.assertTrue(all("GITHUB_TOKEN" not in str(step) for step in steps[:-1]))

    def test_publisher_execution_uses_only_complete_artifacts_and_fixed_branch(self):
        steps = self.workflow["jobs"]["coverage-publish"]["steps"]
        prepare = next(s for s in steps if s["name"] == "Prepare persistent results commit")
        push = next(s for s in steps if s["name"] == "Push coverage commit")
        with tempfile.TemporaryDirectory(prefix="issue64-publisher-") as tmp:
            root = Path(tmp)
            checkout = root / "checkout"
            checkout.mkdir()
            bin_dir = root / "bin"
            bin_dir.mkdir()
            log = root / "git-log.jsonl"
            output = root / "step-output"
            artifact = root / "coverage"
            artifact.mkdir()
            (artifact / "latest.md").write_text("safe summary\n", encoding="utf-8")
            (artifact / "badge.json").write_text("{}\n", encoding="utf-8")
            prepare_file = root / "prepare.sh"
            prepare_file.write_text(prepare["run"], encoding="utf-8")
            push_file = root / "push.sh"
            push_file.write_text(push["run"], encoding="utf-8")
            git_stub = bin_dir / "git"
            git_stub.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "from pathlib import Path\n"
                "args = sys.argv[1:]\n"
                "safe = ['<redacted>' if a.startswith('http.https://github.com/.extraheader=') else a for a in args]\n"
                "with Path(os.environ['FAKE_GIT_LOG']).open('a') as f: f.write(json.dumps(safe) + '\\n')\n"
                "cmd = args[0] if args[0] != '-c' else args[2]\n"
                "if cmd == 'ls-remote':\n"
                "    if os.environ.get('FAKE_LS_REMOTE') == 'fail': sys.exit(128)\n"
                "    if os.environ.get('FAKE_LS_REMOTE') == 'present': print('a' * 40 + '\\trefs/heads/persistent/eval-results')\n"
                "if cmd == 'fetch' and os.environ.get('FAKE_FETCH_FAIL') == '1': sys.exit(1)\n"
                "if cmd == 'diff': sys.exit(1)\n"
                "sys.exit(0)\n", encoding="utf-8")
            git_stub.chmod(0o755)
            self.assertIsNotNone(shutil.which("jq"), "the publisher requires jq")
            env = {"PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
                   "RUNNER_TEMP": str(root), "GITHUB_OUTPUT": str(output),
                   "FAKE_GIT_LOG": str(log)}

            def run(script: Path, **changes):
                output.write_text("", encoding="utf-8")
                log.write_text("", encoding="utf-8")
                return subprocess.run(["bash", str(script)], cwd=checkout,
                                      env={**env, **changes}, capture_output=True,
                                      text=True, timeout=10)

            def calls():
                return [json.loads(line) for line in log.read_text().splitlines()]

            for document in (
                {"status": "unresolved", "unresolved": [],
                 "totals": {"total": 2}, "generated_at": PUBLISH_TIME},
                {"status": "complete", "unresolved": ["acsi-agentskills"],
                 "totals": {"total": 2}, "generated_at": PUBLISH_TIME},
                {"status": "complete", "unresolved": [],
                 "totals": {"total": "2"}, "generated_at": PUBLISH_TIME},
            ):
                with self.subTest(document=document):
                    (artifact / "latest.json").write_text(
                        json.dumps(document), encoding="utf-8")
                    result = run(prepare_file)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(calls(), [])

            (artifact / "latest.json").write_text(json.dumps({
                "status": "complete", "unresolved": [],
                "totals": {"total": 2}, "generated_at": PUBLISH_TIME}), encoding="utf-8")
            for mode in ("fail", "present"):
                result = run(prepare_file, FAKE_LS_REMOTE=mode,
                             FAKE_FETCH_FAIL="1" if mode == "present" else "0")
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any("checkout" in call or "push" in call
                                     for call in calls()))

            result = run(prepare_file, FAKE_LS_REMOTE="present")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("changed=true", output.read_text())
            self.assertIn(["fetch", "origin",
                           "refs/heads/persistent/eval-results:refs/remotes/origin/persistent/eval-results"],
                          calls())
            self.assertIn(["checkout", "-B", "persistent/eval-results",
                           "origin/persistent/eval-results"], calls())
            self.assertTrue((checkout / "coverage" / "2026-10-08T05-41-00Z.json").is_file())

            result = run(push_file, GITHUB_TOKEN="example")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(calls(), [["-c", "<redacted>", "push", "origin",
                                       "HEAD:refs/heads/persistent/eval-results"]])

            result = run(prepare_file, FAKE_LS_REMOTE="absent")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(["checkout", "-B", "persistent/eval-results"], calls())
            self.assertFalse(any(call[0] == "fetch" for call in calls()))

    def test_publisher_rejects_unsafe_checkout_outputs_before_any_write(self):
        prepare = next(s for s in self.workflow["jobs"]["coverage-publish"]["steps"]
                       if s["name"] == "Prepare persistent results commit")
        destinations = ("coverage/latest.json", "coverage/2026-10-08T05-41-00Z.json",
                        "coverage/latest.md", "badges/coverage.json")
        cases = [(path, kind) for path in ("coverage", "badges", *destinations)
                 for kind in ("symlink", "dangling", "wrong-type")]
        for unsafe_path, kind in cases:
            with self.subTest(path=unsafe_path, kind=kind), \
                    tempfile.TemporaryDirectory(prefix="issue64-unsafe-publisher-") as tmp:
                root = Path(tmp)
                checkout = root / "checkout"
                checkout.mkdir()
                artifact = root / "coverage"
                artifact.mkdir()
                (artifact / "latest.json").write_text(json.dumps({
                    "status": "complete", "unresolved": [],
                    "totals": {"total": 2}, "generated_at": PUBLISH_TIME}), encoding="utf-8")
                (artifact / "latest.md").write_text("safe summary\n", encoding="utf-8")
                (artifact / "badge.json").write_text("{}\n", encoding="utf-8")
                # Existing regular outputs must also survive when a later path is unsafe.
                for destination in destinations:
                    path = checkout / destination
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("previous census\n", encoding="utf-8")
                outside = root / "outside"
                outside.mkdir()
                for name in ("latest.json", "2026-10-08T05-41-00Z.json",
                             "latest.md", "coverage.json", "sentinel"):
                    (outside / name).write_text("outside sentinel\n", encoding="utf-8")
                outside_before = {p.name: p.read_bytes() for p in outside.iterdir()}
                missing = root / "missing-outside"
                target = missing if kind == "dangling" else (
                    outside if unsafe_path in ("coverage", "badges") else outside / "sentinel")
                bin_dir = root / "bin"
                bin_dir.mkdir()
                log = root / "git-log.jsonl"
                git_stub = bin_dir / "git"
                git_stub.write_text(
                    "#!/usr/bin/env python3\n"
                    "import json, os, shutil, sys\n"
                    "from pathlib import Path\n"
                    "args = sys.argv[1:]\n"
                    "with Path(os.environ['FAKE_GIT_LOG']).open('a') as f: f.write(json.dumps(args) + '\\n')\n"
                    "if args[0] == 'ls-remote': print('a' * 40 + '\\trefs/heads/persistent/eval-results')\n"
                    "if args[0] == 'checkout':\n"
                    "    path = Path(os.environ['FAKE_UNSAFE_PATH'])\n"
                    "    if path.is_dir(): shutil.rmtree(path)\n"
                    "    else: path.unlink()\n"
                    "    if os.environ['FAKE_UNSAFE_KIND'] == 'wrong-type':\n"
                    "        if len(path.parts) == 1: path.write_text('unsafe directory replacement\\n')\n"
                    "        else: path.mkdir()\n"
                    "    else: path.symlink_to(os.environ['FAKE_UNSAFE_TARGET'])\n"
                    "if args[0] == 'diff': sys.exit(1)\n", encoding="utf-8")
                git_stub.chmod(0o755)
                script = root / "prepare.sh"
                script.write_text(prepare["run"], encoding="utf-8")
                output = root / "step-output"
                output.write_text("", encoding="utf-8")
                result = subprocess.run(
                    ["bash", str(script)], cwd=checkout, capture_output=True, text=True,
                    env={"PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
                         "RUNNER_TEMP": str(root), "GITHUB_OUTPUT": str(output),
                         "FAKE_GIT_LOG": str(log), "FAKE_UNSAFE_PATH": unsafe_path,
                         "FAKE_UNSAFE_KIND": kind, "FAKE_UNSAFE_TARGET": str(target)}, timeout=10)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stderr, "Unsafe coverage output path.\n")
                self.assertEqual({p.name: p.read_bytes() for p in outside.iterdir()}, outside_before)
                self.assertFalse(missing.exists())
                self.assertEqual(output.read_text(), "")
                calls = [json.loads(line) for line in log.read_text().splitlines()]
                self.assertIn(["checkout", "-B", "persistent/eval-results",
                               "origin/persistent/eval-results"], calls)
                self.assertFalse(any(call[0] in ("add", "commit", "push") for call in calls))
                for destination in destinations:
                    if destination != unsafe_path and not destination.startswith(unsafe_path + "/"):
                        self.assertEqual((checkout / destination).read_text(), "previous census\n")

    def test_unresolved_exit_is_informational_except_on_schedule(self):
        gate = next(s for s in self.workflow["jobs"]["coverage"]["steps"]
                    if s["name"] == "Fail an unresolved census")
        self.assertEqual(gate["if"], "steps.census.outputs.result != '0'")
        with tempfile.TemporaryDirectory(prefix="issue64-gate-") as tmp:
            script = Path(tmp) / "gate.sh"
            script.write_text(gate["run"], encoding="utf-8")
            for event, result, expected in (("schedule", "2", 2),
                                            ("pull_request", "2", 0),
                                            ("push", "2", 0),
                                            ("workflow_dispatch", "2", 0),
                                            ("schedule", "1", 1)):
                with self.subTest(event=event, result=result):
                    proc = subprocess.run(["bash", str(script)],
                                          env={"PATH": os.environ.get("PATH", ""),
                                               "EVENT_NAME": event,
                                               "CENSUS_RESULT": result},
                                          capture_output=True, text=True, timeout=10)
                    self.assertEqual(proc.returncode, expected, proc.stderr)
        ci = yaml.load(CI_YML.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        self.assertIn(".github/workflows/propagation.yml", ci["on"]["push"]["paths"])


def ast_imports(path: Path) -> set[str]:
    import ast
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


if __name__ == "__main__":
    unittest.main()
