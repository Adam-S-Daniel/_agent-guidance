#!/usr/bin/env python3
"""Mutation suite for the propagation probes.

Hermetic: no real `claude`, no registry checkout, no network, no wall clock.
`CLAUDE_BIN` points at `test/fake-claude-init`, which SIMULATES skill
resolution (it really reads `$HOME/.claude/skills`, the project's
`.claude/skills` and `installed_plugins.json`) rather than replaying a canned
answer, so the arms exercise their real code path.

The contract every test here is written to: **an assertion nobody has watched
fail is not an assertion.** Each mutation names the exact clause it breaks and
asserts the SPECIFIC verdict and exit code that must result — "it fails
somehow" is satisfied by a probe that fails on everything, which is why
`test_unmutated_run_passes_every_arm` sits alongside them and requires exit 0
across the board on unmutated fixtures.

Run: python3 test/test_propagation.py
"""

from __future__ import annotations

import collections
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

TEST_DIR = Path(__file__).resolve().parent
REPO_ROOT = TEST_DIR.parent
HARNESS_DIR = REPO_ROOT / "harness"
FAKE_CLAUDE_INIT = TEST_DIR / "fake-claude-init"
FAKE_HOOK = TEST_DIR / "fixtures" / "fake-skills-bootstrap.sh"
GOLDEN_STREAM = TEST_DIR / "fixtures" / "init-stream-2.1.231.jsonl"
EVAL_DIR = REPO_ROOT / "evals" / "propagation"

sys.path.insert(0, str(HARNESS_DIR))
import run_propagation  # noqa: E402
from propagation import arms, init_probe  # noqa: E402

NOW = datetime(2026, 8, 14, 12, 0, 0, tzinfo=timezone.utc)
# The arms build their child's environment from init_probe's allowlist, which is
# exactly what would otherwise stop a mutation mode reaching the stub — leaving
# every mutation test silently unmutated and green. Tests widen the allowlist by
# these names only; `test_extra_passthrough_is_empty_in_production` asserts the
# committed value is empty.
STUB_VARS = ("FAKE_INIT_MODE", "FAKE_INIT_DROP", "FAKE_INIT_EXTRA",
             "FAKE_INIT_ACCOUNT", "FAKE_INIT_COMMIT_SHA", "FAKE_HOOK_MODE")
FIXTURE_SKILLS = ("fixture-alpha", "fixture-beta")


def write_skill(directory: Path, name: str, description: str, body: str = "body\n",
                *, crlf: bool = False, extra: dict | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    text = f"---\nname: {name}\ndescription: {description}\n---\n{body}"
    (directory / "SKILL.md").write_bytes(
        text.replace("\n", "\r\n").encode() if crlf else text.encode())
    for relpath, content in (extra or {}).items():
        path = directory / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return directory


def make_registry(root: Path, bundle: str = "acsi",
                  skills=FIXTURE_SKILLS) -> Path:
    """A miniature acsi-agentskills-shaped checkout, plus a skills.lock whose
    digests are computed from the tree it ships — so the lock can never rot
    against it.
    """
    registry = root / "registry"
    for name in skills:
        write_skill(registry / "plugins" / bundle / "skills" / name,
                    name, f"fixture skill {name}.")
    lock = {"registry": "example/agentskills", "ref": "0" * 40,
            "bundles": [bundle],
            "skills": {f"{bundle}/{name}": arms.digest_skill_dir(
                registry / "plugins" / bundle / "skills" / name) for name in skills}}
    (registry / "skills.lock").write_text(json.dumps(lock, indent=2), encoding="utf-8")
    shutil.copy(FAKE_HOOK, registry / "hook.sh")
    return registry


class ProbePrimitiveTests(unittest.TestCase):
    """The stream parser. Every trap here was measured on CLI 2.1.231."""

    def _probe(self, mode="simulate", *, env_extra=None, seed=(), plugins=None):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        scratch = arms.make_scratch(root, "leg")
        for name in seed:
            write_skill(scratch.home / ".claude" / "skills" / name, name, "x")
        env = {"FAKE_INIT_MODE": mode, **(env_extra or {})}
        with mock.patch.dict(os.environ,
                                      {"CLAUDE_BIN": str(FAKE_CLAUDE_INIT)}):
            return init_probe.probe(cwd=scratch.ws, home=scratch.home,
                                    tmpdir=scratch.tmp, env_extra=env, timeout=60)

    def test_selects_init_by_type_and_subtype_not_by_index(self):
        # The real prefix: active_goal, autocompact_state, commands_changed x2,
        # THEN init. A parser taking line 0, or the first `type == "system"`,
        # gets commands_changed — which has no `skills` key at all.
        facts = self._probe(seed=["fixture-alpha"])
        self.assertEqual(facts.init_index, 4)
        self.assertEqual(facts.events[:3],
                         [("active_goal", None), ("autocompact_state", None),
                          ("system", "commands_changed")])
        self.assertIn("fixture-alpha", facts.skills)

    def test_golden_stream_first_system_event_has_no_skills(self):
        # Asserted against the committed capture of a REAL run, so the claim
        # survives this stub being rewritten.
        events = [json.loads(line) for line in
                  GOLDEN_STREAM.read_text(encoding="utf-8").splitlines() if line.strip()]
        first_system = next(e for e in events if e.get("type") == "system")
        self.assertEqual(first_system["subtype"], "commands_changed")
        self.assertNotIn("skills", first_system)
        init = next(e for e in events if e.get("subtype") == "init")
        self.assertEqual(events.index(init), 4)

    def test_init_at_index_zero_also_works(self):
        # Same CLI build, scrubbed environment: init is the FIRST line.
        facts = self._probe("index-zero", seed=["fixture-alpha"])
        self.assertEqual(facts.init_index, 0)
        self.assertIn("fixture-alpha", facts.skills)

    def test_no_init_event_is_a_probe_fault(self):
        with self.assertRaises(init_probe.ProbeError) as caught:
            self._probe("no-init")
        self.assertIn("no system/init event", str(caught.exception))

    def test_absent_skills_key_is_a_fault_not_zero_skills(self):
        with self.assertRaises(init_probe.ProbeError) as caught:
            self._probe("no-skills-key")
        self.assertIn("no `skills` LIST", str(caught.exception))

    def test_skills_not_a_list_is_a_fault(self):
        with self.assertRaises(init_probe.ProbeError) as caught:
            self._probe("skills-not-list")
        self.assertIn("no `skills` LIST", str(caught.exception))

    def test_crashed_cli_is_a_probe_fault(self):
        with self.assertRaises(init_probe.ProbeError):
            self._probe("crash")

    def test_string_bait_matches_nothing(self):
        # Permanently blocks a refactor to a regex over the stream: the literal
        # `"skills": ["acsi:fixture-alpha"]` sits in another event's STRING
        # field. json.loads + a (type, subtype) select cannot see it.
        facts = self._probe("string-bait")
        self.assertNotIn("acsi:fixture-alpha", facts.skills)
        self.assertEqual(facts.skills, ["fixture-builtin-a", "fixture-builtin-b"])

    def test_unparseable_lines_are_counted_never_pattern_matched(self):
        facts = self._probe("garbage-lines", seed=["fixture-alpha"])
        self.assertEqual(facts.unparseable_lines, 1)
        self.assertIn("fixture-alpha", facts.skills)

    def test_duplicate_names_survive(self):
        # Measured on the real CLI: the same skill in ~/.claude/skills and in
        # the project's .claude/skills is listed TWICE. A probe that returned a
        # set would silently lose that.
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        scratch = arms.make_scratch(root, "dup")
        write_skill(scratch.home / ".claude" / "skills" / "dup", "dup", "personal")
        write_skill(scratch.ws / ".claude" / "skills" / "dup", "dup", "project")
        with mock.patch.dict(os.environ,
                                      {"CLAUDE_BIN": str(FAKE_CLAUDE_INIT)}):
            facts = init_probe.probe(cwd=scratch.ws, home=scratch.home,
                                     tmpdir=scratch.tmp, timeout=60)
        self.assertEqual(facts.skill_counts()["dup"], 2)

    def test_env_is_an_allowlist_not_the_ambient_environment(self):
        # The measured leak: inheriting the ambient environment re-synced 17
        # account skills into a scratch HOME (35 skills vs 16). Popping
        # CLAUDE_CODE_SYNC_SKILLS alone did not stop it.
        with mock.patch.dict(os.environ, {
                "CLAUDE_CODE_REMOTE_SESSION_ID": "s", "CLAUDE_CODE_ENTRYPOINT": "remote",
                "CLAUDE_CODE_SYNC_SKILLS": "1", "SOME_OTHER": "x"}):
            env = init_probe.build_env(home=Path("/h"), tmpdir=Path("/t"))
        for leaked in ("CLAUDE_CODE_REMOTE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT",
                       "CLAUDE_CODE_SYNC_SKILLS", "SOME_OTHER"):
            self.assertNotIn(leaked, env)
        self.assertEqual(env["ANTHROPIC_BASE_URL"], init_probe.BLACKHOLE_BASE_URL)

    def test_credentials_are_never_passed_through(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-x"}):
            env = init_probe.build_env(home=Path("/h"), tmpdir=Path("/t"),
                                       extra={"ANTHROPIC_AUTH_TOKEN": "oat"})
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", env)

    def test_scope_flags_are_refused_unless_declared(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        scratch = arms.make_scratch(root, "scope")
        with self.assertRaises(init_probe.ProbeError) as caught:
            init_probe.probe(cwd=scratch.ws, home=scratch.home, tmpdir=scratch.tmp,
                             extra_argv=["--setting-sources", "project"])
        self.assertIn("--setting-sources", str(caught.exception))

    def test_arms_declare_no_scope_flags(self):
        # `--setting-sources project` drops user-level skills (35 -> 18 when
        # measured), so an arm acquiring one silently measures a different
        # surface than the one it names.
        source = (HARNESS_DIR / "propagation" / "arms.py").read_text(encoding="utf-8")
        for flag in init_probe.SCOPE_FLAGS:
            self.assertNotIn(f'"{flag}"', source)


class ChannelAttributionTests(unittest.TestCase):
    def _facts(self, skills, commands=None, plugins=None):
        return init_probe.ProbeFacts(
            init={"skills": skills, "plugins": plugins or []},
            commands=commands or {},
            commands_seen=bool(commands))

    def test_namespace_means_plugin(self):
        self.assertEqual(init_probe.attribute(self._facts(["acsi:x"]))["acsi:x"],
                         "plugin")

    def test_sync_suffix_means_account(self):
        facts = self._facts(["y"], {"y": "desc (claude.ai sync)"})
        self.assertEqual(init_probe.attribute(facts)["y"], "account")

    def test_everything_else_is_local(self):
        self.assertEqual(init_probe.attribute(self._facts(["z"]))["z"], "local")

    # --- #196: a synced skill's name carries a `:` on CLI >= 2.1.269 --------
    # The release notes (read, not measured here) say synced skills are named
    # `anthropic-skills:<name>`, so the namespace rule must not claim them.

    def _probe_account_namespaced(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        scratch = arms.make_scratch(root, "account-namespaced")
        env = {"FAKE_INIT_MODE": "account-namespaced",
               "FAKE_INIT_ACCOUNT": "fixture-synced"}
        with mock.patch.dict(os.environ, {"CLAUDE_BIN": str(FAKE_CLAUDE_INIT)}):
            return init_probe.probe(cwd=scratch.ws, home=scratch.home,
                                    tmpdir=scratch.tmp, env_extra=env, timeout=60)

    def test_a_synced_skill_named_anthropic_skills_is_account_not_plugin(self):
        # Through the production path: the stub emits the stream, `probe`
        # parses it, `attribute` classifies. Red when the `:` rule is checked
        # before the account channel.
        facts = self._probe_account_namespaced()
        self.assertIn("anthropic-skills:fixture-synced", facts.skills)
        self.assertIn("anthropic-skills:fixture-synced",
                      facts.account_named_skills())
        channels = init_probe.attribute(facts)
        self.assertEqual(channels["anthropic-skills:fixture-synced"], "account")
        # The honest built-ins are untouched.
        self.assertEqual(channels["fixture-builtin-a"], "local")

    def test_the_account_namespace_alone_means_account(self):
        # Without `commands_changed` or plugin evidence, the namespace is the
        # only available signal and follows the account naming convention.
        facts = self._facts(["anthropic-skills:y"])
        self.assertFalse(facts.commands_seen)
        self.assertEqual(init_probe.attribute(facts)["anthropic-skills:y"],
                         "account")

    def test_observed_anthropic_skills_plugin_wins_without_account_suffix(self):
        facts = self._facts(
            ["anthropic-skills:y"],
            plugins=[{"name": "anthropic-skills", "path": "/plugins/anthropic-skills"}])
        self.assertFalse(facts.commands_seen)
        self.assertEqual(init_probe.attribute(facts)["anthropic-skills:y"],
                         "plugin")

    def test_account_suffix_wins_beside_observed_same_named_plugin(self):
        facts = self._facts(
            ["anthropic-skills:y"],
            {"anthropic-skills:y": "desc (claude.ai sync)"},
            plugins=[{"name": "anthropic-skills", "path": "/plugins/anthropic-skills"}])
        self.assertEqual(init_probe.attribute(facts)["anthropic-skills:y"],
                         "account")

    def test_malformed_plugin_records_do_not_override_account_fallback(self):
        facts = self._facts(
            ["anthropic-skills:y"],
            plugins=[None, {"name": ["anthropic-skills"], "path": "/plugins/bad"},
                     {"name": {"unexpected": "value"}}])
        self.assertEqual(init_probe.attribute(facts)["anthropic-skills:y"],
                         "account")

    def test_the_account_set_outranks_the_namespace_rule(self):
        # The order itself: any `:`-named skill the account set carries is
        # an account skill, whatever its prefix (`Skill(claude-ai:*)` rules
        # exist in 2.1.282's notes too).
        facts = self._facts(["claude-ai:y"],
                            {"claude-ai:y": "desc (claude.ai sync)"})
        self.assertEqual(init_probe.attribute(facts)["claude-ai:y"], "account")

    def test_a_marketplace_plugin_is_still_a_plugin_beside_a_synced_skill(self):
        # The control: moving the account check first must not swallow the
        # plugin channel.
        facts = self._facts(
            ["acsi:x", "anthropic-skills:y", "z"],
            {"anthropic-skills:y": "d (claude.ai sync)", "z": "plain"})
        self.assertEqual(init_probe.attribute(facts),
                         {"acsi:x": "plugin", "anthropic-skills:y": "account",
                          "z": "local"})

    def test_account_sentinel_is_the_discriminator_that_always_exists(self):
        # commands_changed is NOT emitted on a surface with no account
        # (measured), so the on-disk store is what the guards key on.
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        facts = init_probe.ProbeFacts(init={"skills": []}, home=root)
        self.assertFalse(facts.account_sentinel_present())
        (root / init_probe.ACCOUNT_SENTINEL).mkdir(parents=True)
        self.assertTrue(facts.account_sentinel_present())


class LockAndDigestTests(unittest.TestCase):
    def test_empty_lock_is_refused(self):
        # A lock expecting nothing can never fail — every arm built on it would
        # pass vacuously.
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        path = root / "skills.lock"
        path.write_text(json.dumps({"skills": {}}), encoding="utf-8")
        with self.assertRaises(arms.ArmError) as caught:
            arms.load_lock(path)
        self.assertIn("expects nothing", str(caught.exception))

    def test_digest_changes_on_a_one_byte_edit(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        skill = write_skill(root / "s", "s", "desc")
        before = arms.digest_skill_dir(skill)
        (skill / "SKILL.md").write_text(
            (skill / "SKILL.md").read_text(encoding="utf-8") + "x", encoding="utf-8")
        self.assertNotEqual(before, arms.digest_skill_dir(skill))

    def test_unlabelled_digest_accepts_both_lock_shapes(self):
        # agentskills#87 relabelled lock digests `sha256:<hex>`. Both shapes are
        # live right now — a re-pinned lock carries the label, one that has not
        # been re-pinned yet does not — so the reader must take either.
        bare = "a" * 64
        self.assertEqual(arms.unlabelled_digest("sha256:" + bare), bare)
        self.assertEqual(arms.unlabelled_digest(bare), bare)

    def test_unlabelled_digest_strips_only_the_sha256_label(self):
        # Not a general "drop everything before a colon": a digest labelled with
        # some OTHER algorithm is not silently normalised into agreement with a
        # sha256 one, and the ABSENT sentinel must survive untouched.
        self.assertEqual(arms.unlabelled_digest("md5:" + "a" * 32), "md5:" + "a" * 32)
        self.assertEqual(arms.unlabelled_digest("ABSENT"), "ABSENT")

    def test_digest_matches_the_registry_generator(self):
        # Binds this third copy of the algorithm to acsi-agentskills' own. Skipped
        # where no registry is checked out; propagation.yml runs this suite
        # WITH one, and the live plugin arm is the same binding by other means.
        # The child marker is read FIRST, before any other statement of this
        # sink. A stand-down that runs after other work is only as good as
        # that work's promise never to touch $SKILLS_EVALS_SUITE_CHILD, and
        # test/run_tests.py's scanner no longer takes that promise from
        # anything it cannot read.
        if os.environ.get("SKILLS_EVALS_SUITE_CHILD"):
            raise unittest.SkipTest("child suite run — guarded lock generator")
        registry = run_propagation.resolve_registry(None)
        generator = registry / "scripts" / "generate_skills_lock.py"
        if not generator.is_file():
            self.skipTest(f"no acsi-agentskills checkout at {registry}")
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        skill = write_skill(root / "s", "s", "desc",
                            extra={"scripts/x.py": "print(1)\n"})
        env = dict(os.environ)
        env["SKILLS_EVALS_SUITE_CHILD"] = "1"
        proc = subprocess.run([sys.executable, str(generator), "--digest", str(skill)],
                              capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(arms.digest_skill_dir(skill), proc.stdout)

    def test_hook_verdict_regex_rejects_a_partial_install(self):
        self.assertIsNone(arms.HOOK_OK_RE.match(
            "skills: 8/9 from file:///r@0abcdef — OK"))
        self.assertIsNotNone(arms.HOOK_OK_RE.match(
            "skills: 9/9 from file:///r@0abcdef — OK"))

    def test_hook_skipped_matcher_is_structural_not_exact(self):
        # agentskills 24977ed enriched the decline sentence with an
        # interpolated diagnostic clause; the matcher must accept BOTH the
        # pre-24977ed sentence and the current one, and must still reject
        # anything that changes what the sentence actually asserts.
        declined = arms.hook_declined_for_durable_session
        self.assertTrue(declined(
            "skills: skipped — durable session, "
            "marketplace install is authoritative"))  # old, pre-24977ed
        self.assertTrue(declined(
            "skills: skipped — durable session (entrypoint=unset, no "
            "remote session id), marketplace install is authoritative"))
        self.assertFalse(declined(
            "skills: 9/9 from file:///r@0abcdef — OK"))  # installed, not skipped
        self.assertFalse(declined(
            "skills: skipped — no skills.lock found, "
            "marketplace install is authoritative"))  # a different reason
        self.assertFalse(declined(
            "skills: skipped — durable session (entrypoint=unset, no "
            "remote session id)"))  # missing the marketplace clause


class GuardTests(unittest.TestCase):
    """A guard that did not hold outranks every finding."""

    LOCK = {"skills": {"acsi/fixture-alpha": "0" * 64}}

    def _facts(self, home, skills=("fixture-builtin-a",), commands=None):
        return init_probe.ProbeFacts(init={"skills": list(skills)},
                                     commands=commands or {},
                                     commands_seen=bool(commands), home=home)

    def test_guards_hold_on_a_clean_leg(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        home = root / "home"
        home.mkdir()
        guards = arms.leg_guards("leg", self._facts(home), scratch=root,
                                 declared_env={}, lock=self.LOCK)
        self.assertTrue(all(g.ok for g in guards), [vars(g) for g in guards])

    def test_account_store_reaching_a_leg_trips_a_guard(self):
        # The measured leak, as a mutation: the arm must go INCONCLUSIVE, not
        # green, because every "no account skills" claim it makes is now void.
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        home = root / "home"
        (home / init_probe.ACCOUNT_SENTINEL).mkdir(parents=True)
        guards = arms.leg_guards("leg", self._facts(home), scratch=root,
                                 declared_env={}, lock=self.LOCK)
        bad = [g for g in guards if not g.ok]
        self.assertEqual([g.name for g in bad], ["leg/account-store-absent"])
        self.assertEqual(arms._finish("a", guards, []).status, arms.INCONCLUSIVE)

    def test_surface_variables_are_not_in_the_allowlist(self):
        # What makes the ambient inheritance structurally impossible: none of
        # the three variables the bootstrap hook keys on can arrive by accident.
        self.assertFalse(set(arms.SURFACE_VARS) & set(init_probe.PASSTHROUGH))

    def test_extra_passthrough_is_empty_in_production(self):
        # A debugging widening must not be committed.
        self.assertEqual(init_probe.EXTRA_PASSTHROUGH, ())

    def test_a_control_leg_that_declares_a_surface_variable_trips_a_guard(self):
        # The headline trap as an ARM-EDIT mutation: a control leg carrying
        # SKILLS_BOOTSTRAP_FORCE arms the hook it exists to watch decline.
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        home = root / "home"
        home.mkdir()
        guards = arms.leg_guards("control", self._facts(home), scratch=root,
                                 declared_env={"SKILLS_BOOTSTRAP_FORCE": "1"},
                                 lock=self.LOCK, expect_surface=())
        self.assertIn("control/surface-vars", {g.name for g in guards if not g.ok})

    def test_a_forcing_leg_that_lost_its_surface_variable_trips_a_guard(self):
        # The other direction: an arm that stopped forcing quietly becomes a
        # second copy of its own control leg and passes.
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        home = root / "home"
        home.mkdir()
        guards = arms.leg_guards("arm", self._facts(home), scratch=root,
                                 declared_env={}, lock=self.LOCK,
                                 expect_surface=("SKILLS_BOOTSTRAP_FORCE",))
        self.assertIn("arm/surface-vars", {g.name for g in guards if not g.ok})

    def test_empty_skill_set_trips_the_builtin_floor(self):
        # The arm expecting to find NOTHING is the one most able to pass on a
        # dead CLI. The floor is what stops it.
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        home = root / "home"
        home.mkdir()
        guards = arms.leg_guards("leg", self._facts(home, skills=()), scratch=root,
                                 declared_env={}, lock=self.LOCK)
        self.assertIn("leg/builtin-floor", {g.name for g in guards if not g.ok})

    def test_a_bad_guard_outranks_a_passing_finding(self):
        guards = [arms.Guard("g", False, "broken")]
        result = arms._finish("arm", guards, [arms.Finding("a", True, "fine")])
        self.assertEqual(result.status, arms.INCONCLUSIVE)
        self.assertIn("guards:", result.render())

    def test_failure_render_always_carries_the_guard_block(self):
        result = arms._finish("arm", [arms.Guard("g", True, "ok")],
                              [arms.Finding("a", False, "nope")])
        self.assertEqual(result.status, arms.FAIL)
        self.assertIn("guards:", result.render())


class DeliveryDifferentialTests(unittest.TestCase):
    def test_exact_set_equality_not_a_count(self):
        # An arm that only checked "more skills than before" would pass on any
        # plugin at all.
        delivered = arms._delivered(
            init_probe.ProbeFacts(init={"skills": ["b"]}),
            init_probe.ProbeFacts(init={"skills": ["b", "x", "y"]}))
        self.assertTrue(arms._delivery_finding({"x", "y"}, delivered, "local").ok)
        self.assertFalse(arms._delivery_finding({"x"}, delivered, "local").ok)

    def test_undeclared_extra_is_a_failure_too(self):
        delivered = arms._delivered(
            init_probe.ProbeFacts(init={"skills": []}),
            init_probe.ProbeFacts(init={"skills": ["x", "rogue"]}))
        finding = arms._delivery_finding({"x"}, delivered, "local")
        self.assertFalse(finding.ok)
        self.assertIn("undeclared extras: ['rogue']", finding.detail)

    def test_missing_skill_is_named(self):
        delivered = arms._delivered(
            init_probe.ProbeFacts(init={"skills": []}),
            init_probe.ProbeFacts(init={"skills": ["x"]}))
        finding = arms._delivery_finding({"x", "y"}, delivered, "plugin")
        self.assertFalse(finding.ok)
        self.assertIn("missing: ['y']", finding.detail)


class ArmMutationTests(unittest.TestCase):
    """Every arm assertion, watched failing against the simulator."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.registry = make_registry(self.root)
        self.env = mock.patch.dict(
            os.environ, {"CLAUDE_BIN": str(FAKE_CLAUDE_INIT),
                         "FAKE_INIT_MODE": "simulate"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.seam = mock.patch.object(init_probe, "EXTRA_PASSTHROUGH", STUB_VARS)
        self.seam.start()
        self.addCleanup(self.seam.stop)

    def ctx(self, **overrides):
        run_root = Path(tempfile.mkdtemp(dir=self.root))
        defaults = dict(root=run_root, registry=self.registry,
                        lock_path=self.registry / "skills.lock",
                        lock=arms.load_lock(self.registry / "skills.lock"),
                        hook=self.registry / "hook.sh", bundle="acsi",
                        collision_skill="fixture-alpha", timeout=60)
        defaults.update(overrides)
        return arms.ArmContext(**defaults)

    def run_arm(self, name, **overrides):
        return arms.run_arm(name, self.ctx(**overrides))

    def findings(self, result):
        return {f.assertion: f for f in result.findings}

    # --- unmutated ---------------------------------------------------------

    def test_unmutated_run_passes_every_arm(self):
        # The audit's own audit: a probe that failed on everything would
        # satisfy every mutation below. This is what catches it.
        for name in arms.ARMS:
            with self.subTest(arm=name):
                result = self.run_arm(name)
                self.assertEqual(result.status, arms.PASS, result.render())

    # --- clean-room --------------------------------------------------------

    def test_clean_room_fails_when_a_locked_skill_leaks_in(self):
        result = arms.run_arm("clean-room", self.ctx(
            lock=arms.load_lock(self.registry / "skills.lock")))
        self.assertEqual(result.status, arms.PASS)
        with mock.patch.dict(os.environ, {"FAKE_INIT_MODE": "extra-skill",
                                          "FAKE_INIT_EXTRA": "fixture-alpha"}):
            leaked = self.run_arm("clean-room")
        self.assertEqual(leaked.status, arms.FAIL, leaked.render())
        self.assertIn("leaked: ['fixture-alpha']",
                      self.findings(leaked)["clean-room/no-locked-skills"].detail)

    def test_clean_room_fails_when_the_bundle_namespace_is_present(self):
        # Seeding the plugin into the arm's own HOME is not possible from
        # outside, so drive it the way a stale marketplace install would look:
        # a namespaced name in the stream.
        with mock.patch.dict(os.environ, {"FAKE_INIT_MODE": "extra-skill",
                                          "FAKE_INIT_EXTRA": "acsi:whatever"}):
            result = self.run_arm("clean-room")
        self.assertEqual(result.status, arms.FAIL, result.render())
        self.assertIn("acsi:whatever",
                      self.findings(result)["clean-room/no-lock-namespace"].detail)

    # --- project-mirror ----------------------------------------------------

    def test_project_mirror_fails_when_a_skill_does_not_load(self):
        with mock.patch.dict(os.environ, {"FAKE_INIT_MODE": "drop-one",
                                                   "FAKE_INIT_DROP": "fixture-beta"}):
            result = self.run_arm("project-mirror")
        self.assertEqual(result.status, arms.FAIL, result.render())
        self.assertIn("missing: ['fixture-beta']",
                      self.findings(result)["delivered-set"].detail)

    def test_project_mirror_fails_when_the_registry_lacks_a_locked_skill(self):
        shutil.rmtree(self.registry / "plugins" / "acsi" / "skills" / "fixture-beta")
        result = self.run_arm("project-mirror")
        self.assertEqual(result.status, arms.INCONCLUSIVE)
        self.assertIn("fixture-beta", result.error)

    # --- plugin-marketplace ------------------------------------------------

    def test_plugin_arm_fails_on_a_phantom_lock_entry(self):
        # The live --self-test uses exactly this mutation against the real
        # binary: a lock naming a skill the registry does not ship.
        lock = arms.load_lock(self.registry / "skills.lock")
        lock["skills"]["acsi/phantom"] = "0" * 64
        result = self.run_arm("plugin-marketplace", lock=lock)
        self.assertEqual(result.status, arms.FAIL, result.render())
        self.assertIn("acsi:phantom", self.findings(result)["delivered-set"].detail)

    def test_plugin_arm_fails_when_content_drifts_from_the_lock(self):
        lock = arms.load_lock(self.registry / "skills.lock")
        lock["skills"]["acsi/fixture-alpha"] = "f" * 64
        result = self.run_arm("plugin-marketplace", lock=lock)
        self.assertEqual(result.status, arms.FAIL, result.render())
        self.assertIn("acsi/fixture-alpha", self.findings(result)["plugin/digest"].detail)

    def test_plugin_arm_passes_against_a_sha256_labelled_lock(self):
        # THE REGRESSION (2026-08-19). agentskills#87 relabelled every lock
        # digest `sha256:<hex>`; this arm compared its own bare-hex computation
        # against the recorded value and all 8 skills failed at once —
        # `got a6f71e6d… want sha256:a6f71e6d…` — with the CONTENT identical.
        # It was red on skills-evals' main, and the suite stayed green because
        # make_registry() writes the lock in the BARE shape only, so no test
        # ever fed this arm the shape production had moved to.
        lock = arms.load_lock(self.registry / "skills.lock")
        lock["skills"] = {k: "sha256:" + v for k, v in lock["skills"].items()}
        result = self.run_arm("plugin-marketplace", lock=lock)
        self.assertEqual(result.status, arms.PASS, result.render())

    def test_plugin_arm_still_catches_drift_under_a_labelled_lock(self):
        # The negative control for the test above: tolerating the label must not
        # tolerate a wrong digest that happens to carry one.
        lock = arms.load_lock(self.registry / "skills.lock")
        lock["skills"] = {k: "sha256:" + v for k, v in lock["skills"].items()}
        lock["skills"]["acsi/fixture-alpha"] = "sha256:" + "f" * 64
        result = self.run_arm("plugin-marketplace", lock=lock)
        self.assertEqual(result.status, arms.FAIL, result.render())
        self.assertIn("acsi/fixture-alpha", self.findings(result)["plugin/digest"].detail)

    def test_plugin_arm_does_not_depend_on_the_recorded_git_commit_sha(self):
        # #192. `arm_plugin_marketplace` deliberately does NOT compare the
        # `gitCommitSha` the CLI records in installed_plugins.json: that is
        # the checkout's HEAD, the lock pins the commit whose CONTENT it
        # hashed, and the two differ while every digest matches (see the
        # arm's docstring; introduced with the arm in af91844). CLI 2.1.277
        # and 2.1.280 changed when the CLI writes that value (release notes,
        # not measured here), which is exactly why the arm must not care.
        # Red if the arm ever starts comparing it to the lock's `ref` or
        # requires it to be present.
        lock = arms.load_lock(self.registry / "skills.lock")
        for label, sha in (("a commit other than the lock's ref", "9" * 40),
                           ("the lock's own ref", lock["ref"]),
                           ("no commit recorded", "")):
            with self.subTest(recorded=label):
                with mock.patch.dict(os.environ, {"FAKE_INIT_COMMIT_SHA": sha}):
                    result = self.run_arm("plugin-marketplace", lock=lock)
                self.assertEqual(result.status, arms.PASS, result.render())
                self.assertNotIn("gitCommitSha", result.render())
        self.assertNotEqual("9" * 40, lock["ref"],
                            "the divergent case must actually diverge")

    def test_plugin_arm_fails_when_skills_arrive_unnamespaced(self):
        with mock.patch.dict(os.environ,
                                      {"FAKE_INIT_MODE": "bare-instead-of-ns"}):
            result = self.run_arm("plugin-marketplace")
        self.assertEqual(result.status, arms.FAIL, result.render())
        self.assertIn("missing", self.findings(result)["delivered-set"].detail)

    def _add_second_bundle(self):
        """Give the fixture registry a second plugin and lock both, the shape
        acsi-agentskills' own lock has (acsi-anything-anywhere plus
        acsi-coding-anywhere)."""
        write_skill(self.registry / "plugins" / "second" / "skills" / "fixture-gamma",
                    "fixture-gamma", "fixture skill fixture-gamma.")
        lock = arms.load_lock(self.registry / "skills.lock")
        lock["bundles"] = ["acsi", "second"]
        lock["skills"]["second/fixture-gamma"] = arms.digest_skill_dir(
            self.registry / "plugins" / "second" / "skills" / "fixture-gamma")
        (self.registry / "skills.lock").write_text(json.dumps(lock, indent=2),
                                                   encoding="utf-8")
        return lock

    def test_plugin_arm_installs_every_bundle_the_lock_names(self):
        # THE REGRESSION: the arm installed only the fixture's one `bundle`, so
        # a lock spanning two plugins reported the second plugin's skills as
        # missing although the marketplace channel would deliver them.
        lock = self._add_second_bundle()
        result = self.run_arm("plugin-marketplace", lock=lock)
        self.assertEqual(result.status, arms.PASS, result.render())

    def test_plugin_arm_catches_drift_in_the_second_bundle(self):
        # Negative control for the test above: the second plugin's content is
        # digested against ITS OWN install root, not the first plugin's.
        lock = self._add_second_bundle()
        lock["skills"]["second/fixture-gamma"] = "f" * 64
        result = self.run_arm("plugin-marketplace", lock=lock)
        self.assertEqual(result.status, arms.FAIL, result.render())
        self.assertIn("second/fixture-gamma",
                      self.findings(result)["plugin/digest"].detail)

    def test_plugin_arm_negative_control_fires_on_a_preinstalled_bundle(self):
        # If a namespaced skill is visible BEFORE the install, the arm is
        # reading someone else's plugin, not its own delivery.
        with mock.patch.dict(os.environ, {"FAKE_INIT_MODE": "extra-skill",
                                          "FAKE_INIT_EXTRA": "acsi:preinstalled"}):
            result = self.run_arm("plugin-marketplace")
        self.assertEqual(result.status, arms.FAIL, result.render())
        self.assertIn("before the install",
                      self.findings(result)["plugin/negative-control"].detail)

    def test_an_account_skill_reaching_an_arm_makes_it_inconclusive(self):
        # Not a FAIL: with the account store in play, every "no account skills"
        # claim the arm makes is void, so neither verdict would mean anything.
        with mock.patch.dict(os.environ, {"FAKE_INIT_MODE": "account-leak",
                                          "FAKE_INIT_ACCOUNT": "leaked-from-the-account"}):
            result = self.run_arm("clean-room")
        self.assertEqual(result.status, arms.INCONCLUSIVE, result.render())
        self.assertIn("account-names-absent", result.render())

    # --- bootstrap-hook ----------------------------------------------------

    def test_hook_arm_fails_when_the_surface_guard_does_not_fire(self):
        # The headline trap: a control leg that installs anyway. Without this
        # the "expect nothing" leg passes for entirely the wrong reason.
        with mock.patch.dict(os.environ,
                                      {"FAKE_HOOK_MODE": "ignore-surface-guard"}):
            result = self.run_arm("bootstrap-hook")
        self.assertEqual(result.status, arms.FAIL, result.render())
        self.assertIn("the surface guard did not fire",
                      self.findings(result)["hook/control-installed-nothing"].detail)

    def test_hook_arm_fails_on_a_different_decline_sentence(self):
        # Asserting only "the skills are not visible" would pass here — the
        # hook declined for an unknown reason and said so in words nobody
        # planned for.
        with mock.patch.dict(os.environ, {"FAKE_HOOK_MODE": "wrong-sentence"}):
            result = self.run_arm("bootstrap-hook")
        self.assertEqual(result.status, arms.FAIL, result.render())
        self.assertIn("nothing to do",
                      self.findings(result)["hook/control-verdict"].detail)

    def test_hook_arm_fails_when_the_decline_still_writes_skills(self):
        with mock.patch.dict(os.environ, {"FAKE_HOOK_MODE": "skip-but-install"}):
            result = self.run_arm("bootstrap-hook")
        self.assertEqual(result.status, arms.FAIL, result.render())
        self.assertTrue(self.findings(result)["hook/control-verdict"].ok)
        self.assertFalse(self.findings(result)["hook/control-installed-nothing"].ok)

    def test_hook_arm_fails_on_a_partial_install_verdict(self):
        with mock.patch.dict(os.environ, {"FAKE_HOOK_MODE": "wrong-count"}):
            result = self.run_arm("bootstrap-hook")
        self.assertEqual(result.status, arms.FAIL, result.render())
        self.assertFalse(self.findings(result)["hook/verdict"].ok)

    def test_hook_arm_fails_when_a_skill_does_not_load_after_install(self):
        with mock.patch.dict(os.environ, {"FAKE_INIT_MODE": "drop-one",
                                                   "FAKE_INIT_DROP": "fixture-alpha"}):
            result = self.run_arm("bootstrap-hook")
        self.assertEqual(result.status, arms.FAIL, result.render())
        self.assertIn("missing: ['fixture-alpha']",
                      self.findings(result)["delivered-set"].detail)

    # --- collision-guard ---------------------------------------------------

    def test_collision_arm_fails_when_the_hook_copies_anyway(self):
        # The message alone would pass: the hook says it skipped and copies.
        with mock.patch.dict(os.environ,
                                      {"FAKE_HOOK_MODE": "collision-copies-anyway"}):
            result = self.run_arm("collision-guard")
        self.assertEqual(result.status, arms.FAIL, result.render())
        self.assertTrue(self.findings(result)["collision/verdict"].ok)
        self.assertIn("copied it anyway",
                      self.findings(result)["collision/not-written"].detail)

    def test_collision_arm_fails_when_no_collision_is_reported(self):
        result = self.run_arm("collision-guard", collision_skill="not-in-the-lock")
        self.assertEqual(result.status, arms.FAIL, result.render())
        self.assertFalse(self.findings(result)["collision/verdict"].ok)

    # --- probe faults are never assertion failures -------------------------

    def test_a_reused_scratch_root_is_refused(self):
        # The bug this caught in its own harness: a second run under the same
        # root finds the first run's install already in the "clean" HOME, so
        # its control leg measures the previous run. Refusing is the only safe
        # direction — the arm goes INCONCLUSIVE, never quietly FAIL or PASS.
        ctx = self.ctx()
        first = arms.run_arm("clean-room", ctx)
        self.assertEqual(first.status, arms.PASS)
        second = arms.run_arm("clean-room", ctx)
        self.assertEqual(second.status, arms.INCONCLUSIVE, second.render())
        self.assertIn("already exists", second.error)

    def test_self_test_gets_its_own_scratch_root(self):
        # Same bug, at the seam where it actually shipped: the self-test must
        # not inherit the arms' root, or it reports FAIL (and therefore "the
        # assertions still work") because of a stale marketplace install.
        ctx = self.ctx()
        arms.run_arm("plugin-marketplace", ctx)
        seen = []
        real = arms.run_arm

        def capture(name, given):
            seen.append(given)
            return real(name, given)

        with mock.patch.object(arms, "run_arm", capture):
            ok, line = run_propagation.self_test(ctx)
        self.assertTrue(ok, line)
        # It ran somewhere else, and the phantom is what it injected.
        self.assertNotEqual(seen[0].root, ctx.root)
        self.assertIn("acsi/phantom-skill", seen[0].lock["skills"])

    def test_a_dead_cli_makes_every_arm_inconclusive_never_green(self):
        with mock.patch.dict(os.environ, {"CLAUDE_BIN": "/bin/false"}):
            for name in arms.ARMS:
                with self.subTest(arm=name):
                    result = self.run_arm(name)
                    self.assertEqual(result.status, arms.INCONCLUSIVE, result.render())


class RunnerTests(unittest.TestCase):
    """The CLI surface: exit codes."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.registry = make_registry(self.root)
        self.eval_dir = self.root / "evals" / "propagation"
        self.eval_dir.mkdir(parents=True)
        default_mode = mock.patch.dict(os.environ, {"FAKE_INIT_MODE": "simulate"})
        default_mode.start()
        self.addCleanup(default_mode.stop)
        fixture = (EVAL_DIR / "fixture.yaml").read_text(encoding="utf-8")
        (self.eval_dir / "fixture.yaml").write_text(
            fixture.replace("hook_path: .claude/hooks/skills-bootstrap.sh",
                            "hook_path: hook.sh")
                   .replace("collision_skill: workflow-path-audit",
                            "collision_skill: fixture-alpha")
                   # --self-test plants its phantom under `bundle`, so it must
                   # name a plugin this fixture registry ships.
                   .replace("bundle: acsi-coding-anywhere", "bundle: acsi"),
            encoding="utf-8")

    def run_cli(self, *extra, registry: Path | None = None):
        # Deliberately does NOT set FAKE_INIT_MODE: a test that selects a
        # mutation must not have it overwritten here. setUp establishes the
        # unmutated default. `registry` defaults to the absolute fixture path;
        # only the relative-path regression below overrides it.
        with mock.patch.dict(os.environ, {"CLAUDE_BIN": str(FAKE_CLAUDE_INIT)}), \
                mock.patch.object(init_probe, "EXTRA_PASSTHROUGH", STUB_VARS):
            return run_propagation.main([str(self.eval_dir), "--registry",
                                         str(self.registry if registry is None
                                             else registry),
                                         "--timeout", "60", *extra])

    def test_all_arms_exit_zero_unmutated(self):
        self.assertEqual(self.run_cli(), 0)

    def test_an_assertion_failure_exits_one(self):
        with mock.patch.dict(os.environ, {"FAKE_INIT_MODE": "drop-one",
                                                   "FAKE_INIT_DROP": "fixture-beta"}):
            self.assertEqual(self.run_cli("--arm", "project-mirror"), 1)

    def test_a_probe_fault_exits_two_never_one(self):
        with mock.patch.dict(os.environ, {"FAKE_INIT_MODE": "no-init"}):
            self.assertEqual(self.run_cli("--arm", "clean-room"), 2)

    def test_a_missing_registry_exits_two(self):
        code = run_propagation.main([str(self.eval_dir), "--registry",
                                     str(self.root / "nowhere")])
        self.assertEqual(code, 2)

    def test_a_relative_registry_still_finds_the_hook(self):
        # The CI failure, as a mutation of the CALLER rather than the harness:
        # propagation.yml passed `--registry ../agentskills` at the time (now
        # `--registry ../acsi-agentskills`; the failure mode is unchanged),
        # and the arms run the hook with `cwd` set to a scratch workspace —
        # so a registry left relative was read against THAT directory and
        # bash could not find the hook (rc=127, "No such file or directory").
        # Both hook-running arms
        # went INCONCLUSIVE; every local invocation had passed an absolute path,
        # which is why nothing here saw it first.
        #
        # It drives the real runner from a DIFFERENT working directory on
        # purpose. Asserting that resolve_registry() returns an absolute path
        # would be weaker — it still passes if a later edit resolves the path
        # somewhere the cwd has already moved, which is the whole bug.
        workspace = self.root / "workspace"   # CI's layout: a sibling of the
        workspace.mkdir()                     # registry, naming it `../<name>`
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(workspace)
        relative = Path("..") / self.registry.name
        self.assertFalse(relative.is_absolute(), "the input must stay relative")
        for arm in ("bootstrap-hook", "collision-guard"):  # the two CI reds
            with self.subTest(arm=arm):
                self.assertEqual(
                    self.run_cli("--arm", arm, registry=relative), 0)

    def test_run_record_is_written_when_asked(self):
        record = self.root / "record.json"
        self.assertEqual(self.run_cli("--arm", "clean-room",
                                      "--json", str(record)), 0)
        payload = json.loads(record.read_text(encoding="utf-8"))
        self.assertEqual(payload["arms"][0]["status"], arms.PASS)

    def test_self_test_passes_when_the_arm_can_still_detect_a_bad_lock(self):
        self.assertEqual(self.run_cli("--arm", "clean-room",
                                      "--self-test"), 0)

    def test_self_test_fails_when_the_arm_stops_detecting_anything(self):
        # The blind spot it exists to close: an arm that can no longer see the
        # difference between a correct lock and a wrong one reports PASS.
        ctx = arms.ArmContext(root=self.root, registry=self.registry,
                              lock_path=self.registry / "skills.lock",
                              lock=arms.load_lock(self.registry / "skills.lock"),
                              hook=self.registry / "hook.sh", bundle="acsi",
                              collision_skill="fixture-alpha", timeout=60)
        with mock.patch.object(arms, "run_arm",
                               return_value=arms.ArmResult("plugin-marketplace",
                                                           arms.PASS)):
            ok, line = run_propagation.self_test(ctx)
        self.assertFalse(ok)
        self.assertIn("stopped detecting anything", line)

    def test_unknown_arm_is_a_fault(self):
        self.assertEqual(self.run_cli("--arm", "nope"), 2)

    def test_committed_fixture_names_only_real_arms(self):
        fixture = run_propagation.load_fixture(EVAL_DIR)
        self.assertTrue(set(fixture["arms"]) <= set(arms.ARMS))
        self.assertEqual(sorted(fixture["arms"]), sorted(arms.ARMS))

class _DryRunStepContract:
    """The shared contract for a scheduled step that files a tracking issue.

    propagation.yml's `report` (the probes are failing) is the one workflow
    here that has one. account-store-drift.yml's `react` (the account store
    has drifted) used to be a second, and this was written as a mixin rather
    than folded into `DispatchAndDryRunTests` directly because the two
    reported different facts to different readers under the same SHAPE — a
    shape that kept being got wrong. `react` and its workflow were retired
    2026-09-28 along with the Tier-3 audit they reacted to (see
    evals/propagation/ROUTINE.md, now HISTORY); the mixin stays a mixin
    because the shape it pins is a real invariant a future scheduled-write
    step could need again, not because a second subclass still exists today.

    A MIXIN rather than a base TestCase, so unittest never collects it on its
    own: a contract class with no workflow bound to it passes by checking
    nothing, which is the failure every guard below is written against. Each
    subclass binds WORKFLOW, JOB, WRITE_SPELLINGS, PROLOGUE_ALLOWLIST and
    `select_step`, and adds whatever its own workflow needs on top.

    It was written for propagation.yml first, and everything below is the
    reason why.

    propagation.yml stays runnable by hand, and its dry run never writes.

    A probe you cannot run on demand is a probe you cannot VERIFY on demand. A
    fix to the arms merged and confirming it meant waiting for a push or the
    05:41 cron, because the dispatch came back `422 Workflow does not have
    'workflow_dispatch' trigger`. Losing the trigger again would be silent —
    nothing red, just a button that is not there on the day someone needs it —
    so it is pinned here rather than left to review.

    The dry run is the other half. `report` is the only place in this repo that
    WRITES anything (gh issue create / comment, the sole `issues: write` grant
    in the workflow); the runner it invokes, `harness/run_propagation.py`, has
    no write path at all. So "dry run" can only mean: run the detection, run
    the dedupe lookup, and stop one line short of the two gh write calls. Two
    ways that silently stops being true — the flag getting built inline as
    `${{ inputs.dry_run && 'x' || 'y' }}`, which is not a ternary and fires its
    `||` branch on any falsy `&&` branch (example-site-platform run 32280743541 skipped
    a whole scan that way while printing a healthy result), and a later edit
    moving a write above the bail-out — are what the last three tests exist
    for.

    That last one is guarded twice over, and deliberately from opposite
    directions. The narrow test names the two `gh issue` calls and pins them
    below the bail-out; the allowlist test names nothing that writes at all and
    instead requires every statement ABOVE the bail-out to be a shape known to
    be harmless. The narrow one alone was not enough — a review hoisted
    `gh api -X POST …/issues`, then `curl -X POST`, and it stayed green both
    times, because a denylist of spellings can only catch the spellings
    somebody already listed.

    Everything here goes through a real YAML parser. A line scan cannot see
    which job an `if:` belongs to, and this suite is the one that would have to
    catch that.
    """

    # The one statement above the bail-out that may run a command inside
    # `$( )`, named so the allowlist below and the test can agree on which it
    # is without either of them counting entries.
    DEDUPE_LOOKUP = "the dedupe lookup"

    # Every sequence that can put a SECOND command into one statement. Counted
    # on EVERY prologue statement against the budget its allowlist entry
    # declares, because matching a whole-line pattern is not enough on its own:
    # a pattern ending in `.*` swallows a separator happily, so
    # `echo hi; gh api -X POST …/issues` fullmatches `echo .*` and an
    # adversarial review walked exactly that past an earlier version of this
    # guard. Longest-first so `&&` is not counted as two `&`.
    COMMAND_SEPARATORS = (r"\$\(", r">\(", r"<\(", r"&&", r"\|\|", ";", r"\|", "&", "`")
    SEPARATOR_RE = re.compile("|".join(COMMAND_SEPARATORS))

    def setUp(self):
        import yaml
        # Read outside any try/except: a missing or unparseable workflow must
        # blow up here, not degrade into a test that quietly asserts nothing.
        self.doc = yaml.safe_load(self.WORKFLOW.read_text(encoding="utf-8"))
        # A bare `on:` key is the YAML 1.1 boolean True once parsed.
        self.triggers = self.doc.get("on", self.doc.get(True))
        self.job = self.doc["jobs"][self.JOB]
        # WHICH step gets linted is the subclass's call, and it is an assertion
        # rather than an index. Every step in a job runs under that job's
        # `issues: write` grant, so a job that grows a step this suite does not
        # know about has grown a write path nothing checks — each subclass says
        # below how it rules that out for its own workflow.
        self.step = self.select_step()
        self.assertIn("run", self.step,
                      "the step this suite lints must be the scripted one; "
                      f"found {self.step.get('uses')!r}")

    def _dry_run_env_var(self) -> str:
        """The env name carrying the dispatch input, taken from the workflow.

        Derived rather than hard-coded so a rename cannot leave the shell
        testing one variable while the workflow sets another — which would
        make the dry run a no-op that still passes every other test here.
        """
        wired = {k: v for k, v in (self.step.get("env") or {}).items()
                 if "inputs.dry_run" in str(v)}
        self.assertEqual(len(wired), 1,
                         "exactly one env entry may carry the dry_run input "
                         f"into the step; found {sorted(wired)}")
        return next(iter(wired))

    def test_the_workflow_can_be_dispatched_with_a_dry_run_input(self):
        self.assertIn("workflow_dispatch", self.triggers,
                      "without workflow_dispatch a probe fix cannot be "
                      "verified until the next push or the daily cron")
        inputs = (self.triggers["workflow_dispatch"] or {}).get("inputs") or {}
        self.assertIn("dry_run", inputs,
                      "a dispatch with no dry_run input can only run the probes "
                      "by also arming the tracking-issue write")
        self.assertIs(inputs["dry_run"]["default"], True,
                      "the dispatch defaults to dry — a verification run that "
                      "files a real tracking issue leaves a human tidying up "
                      "after the tool they reached for to save work")

    def test_the_flag_reaches_the_shell_as_a_bare_input_reference(self):
        expr = str(self.step["env"][self._dry_run_env_var()])
        for operator in ("&&", "||"):
            self.assertNotIn(
                operator, expr,
                f"{expr!r} must stay a bare `${{{{ inputs.dry_run }}}}` and the "
                "flag be built in shell. `a && b || c` is not a ternary: GitHub "
                "returns c whenever b is falsy, and an empty string is falsy, "
                "which is how example-site-platform shipped an opt-out that fired "
                "unconditionally while reporting a healthy result")

    def test_no_workflow_expression_is_interpolated_into_the_script(self):
        """The fleet rule, and it is not only about the obvious spelling.

        `${{ }}` is substituted into a `run:` body BEFORE the shell sees any of
        it, so the rendered command is echoed into a public log and evaluated
        as shell — which is why every value these steps read arrives through
        `env:` instead. `FreshnessGateEventPolicyTests` already pins that for
        the gate step; the two steps that hold `issues: write` had no such
        assertion, and the gap is not theoretical: a comment added here while
        fixing an unrelated defect explained the rule by QUOTING it, and an
        empty expression in a shell comment is a workflow-level syntax error
        that no test, no shell parse and no reading of the diff caught. GitHub
        does not know `#` starts a comment — it expands the whole block.
        """
        self.assertNotIn(
            "${{", self.step["run"],
            f"the {self.JOB!r} job's scripted step interpolates a workflow "
            "expression into its `run:` body. Values reach the script through "
            "`env:` — including inside comments, which are expanded like every "
            "other line here.")

    def _first(self, lines, predicate, what, after=-1):
        """Index of the first matching line after `after`, or a failure.

        Never returns a sentinel: a missing landmark has to fail loudly here,
        because every ordering assertion built on one is trivially true
        against an index nobody found.
        """
        hits = [i for i, line in enumerate(lines) if i > after and predicate(line)]
        self.assertTrue(hits, f"no {what} in the report step's script")
        return hits[0]

    def _bail_out(self, lines):
        """(index of the `if` guarding the dry run, index of its `exit 0`)."""
        var = self._dry_run_env_var()
        guard = self._first(
            lines, lambda l: l.lstrip().startswith("if ") and f'"${var}"' in l,
            f"shell test on ${var}")
        return guard, self._first(lines, lambda l: l.strip() == "exit 0",
                                  "`exit 0`", after=guard)

    @staticmethod
    def _heredoc_delimiter(text):
        """(delimiter, is the body EXPANDED?) for a heredoc `text` opens, else None.

        Quoting the delimiter is what makes a heredoc body inert. `<<'EOF'`
        is literal; bare `<<EOF` is expanded by the shell, so a `$( )` in the
        body RUNS — which is a command above the dry-run bail-out that the
        body is not obviously carrying. This workflow uses the bare form, so
        the distinction is live and not theoretical, and it is why the
        delimiter's quoting is returned rather than discarded.

        `<<` only opens a heredoc where the SHELL sees it as an operator.
        Inside a quoted string it is ordinary prose, and a scanner that cannot
        tell the difference stops reading the script at the first line that
        merely mentions it. An adversarial review walked a write past this
        guard exactly that way — `echo "if it recurs see << ESCALATION in the
        runbook"` took ESCALATION for a delimiter, every statement after it
        including a bare `gh api -X POST …/issues` became heredoc body, and
        none of them was ever checked. So quoting is tracked here rather than
        approximated: this walks the text, keeps single/double-quote state, and
        only considers a `<<` found outside both.
        """
        in_single = in_double = False
        i = 0
        while i < len(text):
            ch = text[i]
            if ch == "\\" and not in_single:
                i += 2                      # escaped character, quote or not
                continue
            if ch == "'" and not in_double:
                in_single = not in_single
            elif ch == '"' and not in_single:
                in_double = not in_double
            elif ch == "<" and not in_single and not in_double \
                    and text.startswith("<<", i):
                # The quotes around a delimiter must MATCH (`<<'EOF'`, not
                # `<<'EOF`), hence the backreference. `<<<` is a here-string,
                # not a heredoc, and fails this match — keep scanning rather
                # than concluding there is no heredoc on the line.
                found = re.match(r"<<-?\s*([\"']?)([A-Za-z_]\w*)\1", text[i:])
                if found:
                    return found.group(2), not found.group(1)
                i += 2
                continue
            i += 1
        return None

    def _statements(self, lines):
        """[(line index, text, "command" | "heredoc")] for everything the shell EVALUATES.

        Three things stop a line from meaning what it looks like it means, and
        all three are handled here rather than in each assertion. A heredoc
        body is data the shell never executes — `cat > … <<EOF` writes a file
        on the runner — so its prose would otherwise be judged as commands.
        Comments and blanks are not statements. And a backslash continuation
        splits one statement over several lines: the dedupe lookup is three
        lines long, so anything reasoning per-line reads two thirds of it as
        bare fragments.

        Both ways this parse can quietly stop covering the script — ending
        mid-continuation, or ending inside a heredoc that never closed — are
        assertions, not silent truncations. Everything downstream is an
        allowlist, and an allowlist over the statements a broken parse happened
        to reach passes without having checked the ones it did not.
        """
        statements, heredoc, expands, pending = [], None, False, None
        for i, raw in enumerate(lines):
            if heredoc is not None:
                if raw.strip() == heredoc:
                    heredoc, expands = None, False
                elif expands:
                    # Not inert: the delimiter was unquoted, so the shell
                    # expands this line before `cat` ever sees it.
                    statements.append((i, raw.strip(), "heredoc"))
                continue
            line = raw.strip()
            if pending is None:
                if not line or line.startswith("#"):
                    continue
                start, text = i, line
            else:
                start, text = pending[0], f"{pending[1]} {line}"
            if text.endswith("\\"):
                pending = (start, text[:-1].rstrip())
                continue
            pending = None
            opened = self._heredoc_delimiter(text)
            heredoc, expands = opened if opened else (None, False)
            statements.append((start, text, "command"))
        self.assertIsNone(pending,
                          "the report step's script ends inside a backslash "
                          "continuation, so it does not parse — and nothing "
                          "built on that parse can be trusted")
        self.assertIsNone(
            heredoc,
            f"the report step's script opens a heredoc delimited by {heredoc!r} "
            "and never closes it, so everything after that point was read as "
            "data and never checked as a command")
        return statements

    def test_the_issue_writes_sit_below_the_dry_run_bail_out(self):
        lines = self.step["run"].splitlines()
        guard, bail = self._bail_out(lines)

        # Detection first: the dedupe lookup must run in a dry run too, or the
        # dry run proves the flag works and nothing about the search that
        # decides create-vs-comment.
        lookup = self._first(lines, lambda l: "gh issue list" in l, "`gh issue list`")
        self.assertLess(lookup, guard,
                        "the dedupe lookup must run before the dry-run bail-out")

        writes = [i for i, line in enumerate(lines)
                  if any(call in line for call in self.WRITE_SPELLINGS)]
        self.assertEqual(
            len(writes), len(self.WRITE_SPELLINGS),
            f"expected exactly {list(self.WRITE_SPELLINGS)}, one line each; "
            f"found {len(writes)}. This counts literal `gh issue` spellings "
            "and nothing else: a write spelled `gh api`, `curl` or anything "
            "the GitHub CLI grows next is INVISIBLE here. Catching an "
            "arbitrary write is "
            "test_nothing_above_the_dry_run_bail_out_can_write's job — this "
            "test only pins these known calls to their side of the line.")
        for write in writes:
            self.assertGreater(
                write, bail,
                f"script line {write + 1} writes to the tracking issue above the "
                f"dry-run bail-out, so a dry run would write after all: "
                f"{lines[write].strip()!r}")

    def test_nothing_above_the_dry_run_bail_out_can_write(self):
        """The prologue is an ALLOWLIST of readers, not a denylist of writes.

        Its sibling above finds writes by matching the two literal spellings
        `gh issue create` and `gh issue comment`, and an adversarial review
        proved that is the wrong direction — twice. It hoisted
        `gh api -X POST "/repos/$GITHUB_REPOSITORY/issues"` above the bail-out
        and the suite stayed green; it did it again with `curl -X POST` and
        the suite stayed green again. Both are real issue writes; neither is
        spelled "gh issue". A denylist can only ever name the writes somebody
        already thought of, and the write nobody thought of is the whole
        reason this guard exists.

        So this inverts it. Every statement up to and including the dry-run
        `exit 0` must match one of PROLOGUE_ALLOWLIST — a short, stable list
        of shapes that demonstrably cannot reach the tracking issue. Nothing
        in that list names a write, so nothing in it has to be kept in step
        with the vocabulary of the GitHub CLI: `gh api`, `curl`, `wget`,
        `python3 -c`, a third `gh issue create`, all fail identically.

        Two rounds of review then showed that inverting the direction is
        necessary and not sufficient, because BOTH remaining gaps let a write
        reach the tracking issue without ever being compared to the list:

        * A whole-line pattern that ends in `.*` matches a statement with a
          second command bolted onto it, so `echo hi; gh api -X POST …/issues`
          fullmatched `echo .*` and passed. Hence COMMAND_SEPARATORS: what a
          statement may CONTAIN is budgeted per entry, separately from what it
          looks like.
        * A statement that merely mentioned `<<` inside a quoted string made
          the parser treat the rest of the script as heredoc body, so the
          statements after it — a bare `gh api` write among them — were never
          checked at all. Hence the quote-aware `_heredoc_delimiter`, and the
          assertion that a heredoc actually closes.
        * And a heredoc body is only inert when its DELIMITER is quoted. This
          workflow opens `<<EOF` bare, so the shell expands the body: a
          `$(gh api -X POST …/issues)` sitting in the middle of the issue
          prose runs, above the bail-out, while looking like text. Skipping
          bodies wholesale therefore skipped a live command, so an expanded
          body is now read for substitutions and nothing else.

        Both are the same failure in different clothes: the guard examined
        fewer things and said nothing about it. The dedupe-lookup landmark
        below exists for that reason and is asserted before the list is
        applied.

        The cost is a red test the day this step is legitimately rewritten.
        That is the direction the failure should point: widening the allowlist
        is a deliberate line in a diff somebody reads, whereas failing to
        anticipate a spelling is nothing at all.
        """
        lines = self.step["run"].splitlines()
        _, bail = self._bail_out(lines)
        prologue = [entry for entry in self._statements(lines) if entry[0] <= bail]

        # Vacuity control, asserted BEFORE the allowlist is applied: an
        # allowlist run over zero statements passes on every input it never
        # saw, which is precisely the shape of failure this test exists to
        # close. The dedupe lookup is the landmark that proves the parse
        # reached the real script — it is the only statement up here that is
        # neither a shell keyword nor a print, so if the parse collapsed to
        # nothing, or the heredoc scanner swallowed the rest of the file, it
        # is the first thing to go missing.
        lookup_pattern = next(pattern for what, pattern, _ in self.PROLOGUE_ALLOWLIST
                              if what == self.DEDUPE_LOOKUP)
        lookups = [text for _, text, source in prologue
                   if source == "command" and re.fullmatch(lookup_pattern, text)]
        self.assertEqual(
            len(lookups), 1,
            "expected exactly one dedupe lookup above the dry-run bail-out; found "
            f"{len(lookups)} among {len(prologue)} statement(s). Either the script "
            "changed shape or this test parsed something other than it — and an "
            "allowlist with nothing to check passes without checking anything.")

        for start, text, source in prologue:
            where = f"script line {start + 1}"  # of the step's `run:` block, not the file
            if source == "heredoc":
                # The body of a heredoc whose delimiter was NOT quoted. It is
                # not a command, so the allowlist has nothing to say about its
                # shape — but the shell expands it before `cat` receives it,
                # so a substitution in it runs exactly where a command would.
                # Only that is checked; the prose is free to say anything.
                for form in ("$(", "`"):
                    self.assertNotIn(
                        form, text,
                        f"{where} sits in the body of a heredoc opened with an "
                        f"UNQUOTED delimiter, above the dry-run bail-out: {text!r}. "
                        "The shell EXPANDS such a body, so this substitution runs "
                        "on a dry run like any other command. Quote the delimiter "
                        "(`<<\'EOF\'`) if the body is meant to be literal, or move "
                        "the substitution below the bail-out.")
                continue
            allowed = [(what, budget) for what, pattern, budget
                       in self.PROLOGUE_ALLOWLIST if re.fullmatch(pattern, text)]
            self.assertTrue(
                allowed,
                f"{where} runs above the dry-run bail-out and is not one of "
                f"the shapes known not to write: {text!r}. Nothing may run up there "
                "that PROLOGUE_ALLOWLIST does not vouch for — if it genuinely cannot "
                "reach the tracking issue, add it there and say why; if it can, it "
                "belongs below the bail-out with the other writes.")
            what, budget = allowed[0]
            # Matching a shape is necessary and not sufficient. Every allowlist
            # pattern that ends in `.*` will match a statement with a second
            # command bolted onto it, so what a statement is ALLOWED to contain
            # is counted separately from what it looks like: anything over the
            # declared budget is a command this guard never examined.
            found = collections.Counter(self.SEPARATOR_RE.findall(text))
            for sequence, count in sorted(found.items()):
                self.assertLessEqual(
                    count, budget.get(sequence, 0),
                    f"{where} puts {count} {sequence!r} into {what}, above the "
                    f"dry-run bail-out, where {budget.get(sequence, 0)} are "
                    f"accounted for: {text!r}. That is room for a second command "
                    "— a write chained on with `;`, substituted in with `$( )` or "
                    "piped through `>( )` reaches the tracking issue exactly like "
                    "the calls below the bail-out do.")


class DispatchAndDryRunTests(_DryRunStepContract, unittest.TestCase):
    """The contract above, bound to propagation.yml's `report` job."""

    WORKFLOW = REPO_ROOT / ".github" / "workflows" / "propagation.yml"
    JOB = "report"
    # One line each, below the bail-out. `gh issue comment` rather than an
    # edit: this issue is a running log of failing probe runs, not a single
    # edited-in-place statement of state.
    # The close call joined them when the job gained a green path. It is a write
    # like the other two, and a write this tuple does not name is one this test
    # never pins to its side of the bail-out — the sibling allowlist test would
    # still refuse to let it sit above there, but nothing would notice it
    # QUIETLY MOVING, which is the failure this narrow test is for.
    WRITE_SPELLINGS = ("gh issue create", "gh issue comment", "gh issue close")

    # (what it is, WHOLE-LINE pattern, separator budget) for every statement
    # shape permitted above the dry-run bail-out. The budget is `{sequence:
    # exact count}` and defaults to none at all: a shape that needs a separator
    # has to say which and how many, so an extra one is a failure even inside a
    # statement whose pattern still matches. Nothing here names a write — that
    # is the entire design; see the test's docstring.
    PROLOGUE_ALLOWLIST = (
        ("shell options", r"set -euo pipefail", {}),
        ("a heredoc into the runner's own temp dir, which is not GitHub",
         r"""cat > "\$RUNNER_TEMP/[\w.-]+" <<(?:'EOF'|EOF)""", {}),
        ("reading that temp file back into the log",
         r'''cat "\$RUNNER_TEMP/[\w.-]+"''', {}),
        # One `$( )` for the substitution itself and one `|` for the jq filter
        # inside it. That budget is what rejects `; gh issue create` chained on
        # the end, and `| tee >(gh api …)` piped into the middle.
        (_DryRunStepContract.DEDUPE_LOOKUP,
         r"number=\$\(gh issue list .*\)", {"$(": 1, "|": 1}),
        # `if gh api …; then` is deliberately NOT this shape: the pattern is
        # anchored on `[` … `]`, and the budget then allows only the one `;`
        # that `]; then` needs.
        ("a `[` test", r"if \[ .* \]; then", {";": 1}),
        ("a shell keyword", r"else", {}),
        ("a shell keyword", r"fi", {}),
        ("a print", r"echo .*", {}),
        ("the bail-out itself", r"exit 0", {}),
    )
    # And the honest boundary, because a guard that claims more than it does is
    # how the test below got written in the first place. This governs the
    # report job's ONE step — `setUp` fails loudly if that job grows a second
    # step of ANY kind, scripted or `uses:`, because a `uses:` step needs no
    # shell to file an issue and would otherwise sit above this script entirely
    # unexamined. It says nothing about any OTHER job: `issues: write` is
    # granted to `report` alone today, but nothing here pins that grant, so a
    # write added under a different job that declared its own is outside this
    # test's reach.
    #
    # NO CREDENTIAL CENSUS HERE, and that is the one-step rule doing the work
    # rather than an omission. A job with several steps under one
    # `issues: write` grant would raise a real question — "which of them
    # holds a token", including one declared at `jobs.<id>.env:` or workflow
    # scope, since an inherited `env:` arms every step at once. `report`
    # never raises it: it is exactly one step, so `env:` inherited from
    # anywhere reaches only the write step, which already declares
    # `GH_TOKEN` legitimately. Measured rather than reasoned about: both
    # splices were run against every test in this class and all 7 stayed OK,
    # which is the correct result and not a gap. What a workflow-level
    # `env:` WOULD reach is `gate` and `arms` — the other jobs, already
    # declared out of scope above, and bounded in fact by the
    # workflow-level `permissions: contents: read` that neither overrides.
    # If `report` ever legitimately needs a second step, it needs this same
    # census too.

    def select_step(self):
        # EVERY step, not just the scripted ones. Filtering to `run:` steps left
        # a `uses: peter-evans/create-issue-from-file` step able to sit above
        # this script and file the issue with no shell at all — invisible to a
        # guard that only ever reads the script. The report job is one step; if
        # it legitimately needs another, the prologue guard below has to be
        # re-scoped before that lands, which is what this failure says.
        steps = self.job["steps"]
        self.assertEqual(
            len(steps), 1,
            "the report job is expected to be exactly one step; found "
            f"{[s.get('name') or s.get('uses') for s in steps]}. Every step in "
            "this job runs under its `issues: write` grant, and only the "
            "scripted one is checked against the dry-run bail-out.")
        return steps[0]

    def test_the_report_job_is_reachable_on_a_dispatch(self):
        # Otherwise dry_run is a knob wired to nothing: the only job it governs
        # could never run by hand, and the input would read as coverage it is
        # not providing.
        self.assertIn("workflow_dispatch", self.job["if"],
                      "the report job's `if:` must admit workflow_dispatch, or "
                      f"the dry_run input governs nothing: {self.job['if']!r}")

    def test_the_report_job_is_scheduled_on_a_green_run_as_well(self):
        """The close path's precondition, and it is the `if:` and not the shell.

        `failure()` alone is why nothing in this repo ever closed this issue:
        the only job that can write was not scheduled on the run that PROVED
        the probes were healthy again, so a repaired probe left its issue open
        until a person happened to notice. An issue whose presence means "broke
        once" rather than "is broken" is one people stop reading, which is the
        same death as never filing it.

        `always()` is the wrong fix and is refused here: it also fires on a
        CANCELLED upstream job, which measured nothing at all. Closing a live
        finding on the strength of a run that never completed is the one write
        in this step that the next morning's run cannot take back.
        """
        condition = self.job["if"]
        self.assertIn(
            "success()", condition,
            "a green scheduled run must reach the report job or nothing ever "
            f"closes the tracking issue: {condition!r}")
        self.assertIn(
            "failure()", condition,
            f"a red run must still reach it: {condition!r}")
        self.assertNotIn(
            "always()", condition,
            "always() also fires on a cancelled run, which measured nothing: "
            f"closing on it retracts a live finding on no evidence: {condition!r}")


class _StubbedShellStep:
    """Run a workflow step's real `run:` body with the commands it calls stubbed.

    Everything else in this file that reads a workflow reads its SHAPE. That is
    the right instrument for "no write sits above the bail-out", and the wrong
    one for "which flag does this event get" — a script can build an array
    correctly and then never expand it, and a shape test passes on it while
    every event silently takes the same branch. So the two questions that are
    about BEHAVIOUR execute the script instead, with a stub first on `PATH`
    standing in for the one binary it invokes.

    Deterministic in the sense this suite means it: bash, one shell script and
    one file per run. No clock, no network, no CLI, nothing that varies between
    a laptop and a runner.
    """

    @staticmethod
    def _stub(directory: Path, name: str, body: str) -> None:
        path = directory / name
        path.write_text(f"#!/bin/sh\n{body}", encoding="utf-8")
        path.chmod(0o755)

    def _bash(self, script: str, env: dict, stub_dir: Path):
        # A CLOSED environment, not os.environ plus overrides: an ambient
        # GITHUB_* or GH_TOKEN leaking in from the session running the tests
        # would make a `set -u` failure look like a pass, and on a real runner
        # it would be a live credential. PATH is the one thing inherited, and
        # the stub dir goes in front of it.
        proc = subprocess.run(
            ["bash", "-c", script],
            env={"PATH": f"{stub_dir}:{os.environ.get('PATH', '')}", **env},
            capture_output=True, text=True)
        self.assertEqual(
            proc.returncode, 0,
            f"the step's script exited {proc.returncode}: {proc.stderr.strip()}")
        return proc


class ReportStepBehaviourTests(_StubbedShellStep, unittest.TestCase):
    """What the report step DOES with each pair of job results.

    `DispatchAndDryRunTests` pins where the writes may sit; nothing there says
    which one happens. Three behavioural defects, invisible to a shape test,
    are what this class was written for:

    * the body diagnosed a cause it could not know. Before 2026-09-28 `gate`
      also carried a freshness check on a claude.ai Routine, and every
      scheduled failure this workflow ever had under that check — five of
      them, run 32452792300 included — was the `gate` job on a
      `[reported-failure]` verdict with all five arms green, which the old
      body's single "look at the registry" diagnosis could not have named
      (see evals/propagation/ROUTINE.md, now HISTORY). The body now prints
      the two job results and describes both halves, so the assertions are
      that the results really reach it and that a red gate is diagnosed as
      this repository's own fault, never the registry's.
    * nothing ever closed the issue. A green run reaching the job is one half
      (`test_the_report_job_is_scheduled_on_a_green_run_as_well`); this is the
      other — the script, run with both results green, has to reach the close
      call and not the create one.
    * and then the close reached too far. `schedule` is the only trigger
      guaranteed to be testing `main`; a `workflow_dispatch` can target any
      branch, so a green dispatch from an unmerged fix would retract a
      finding that the next scheduled run — still red on `main` — immediately
      re-files. Which EVENT the step is running under is therefore an input
      to this suite, not ambient context — hence `run_step(event=…)`.

    `gh` is stubbed rather than the whole script paraphrased, so the branch
    under test is the real one, including the dedupe substitution feeding it.
    """

    WORKFLOW = REPO_ROOT / ".github" / "workflows" / "propagation.yml"

    def setUp(self):
        import yaml
        doc = yaml.safe_load(self.WORKFLOW.read_text(encoding="utf-8"))
        steps = doc["jobs"]["report"]["steps"]
        self.assertEqual(len(steps), 1)
        self.step = steps[0]
        self.temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)

    def run_step(self, *, gate, arms_result, open_issue="", dry_run="",
                 event="schedule"):
        """(gh calls, stdout, the failure body, the recovery body).

        The stub answers `gh issue list` with `open_issue` — the dedupe lookup
        is a command substitution, so answering it is what selects the
        create-vs-comment branch — and records the first three arguments of
        every other call. Three arguments, not all of them: the close call
        carries a whole issue body in `--comment`, and a log that swallowed it
        would be asserting on prose rather than on which write happened.

        `event` DEFAULTS TO THE SCHEDULE rather than to the empty string, and
        the default is doing work: the schedule is the surface every write here
        is designed for, so the cases below keep meaning what they meant before
        the event became an input. It is a real parameter because the step's
        outcome now depends on it — `_bash` builds a CLOSED environment, so a
        case that forgot to pass one would die on `set -u` rather than quietly
        measuring whichever event the ambient session happened to be running
        under.
        """
        stub_dir = (self.temp /
                    f"stub-{event}-{gate}-{arms_result}-"
                    f"{open_issue or 'none'}-{dry_run or 'live'}")
        stub_dir.mkdir(parents=True)
        runner_temp = stub_dir / "runner-temp"
        runner_temp.mkdir()
        gh_log = stub_dir / "gh.log"
        self._stub(stub_dir, "gh",
                   'if [ "$1" = "issue" ] && [ "$2" = "list" ]; then\n'
                   '  printf "%s" "$GH_OPEN_ISSUE"\n'
                   '  exit 0\n'
                   'fi\n'
                   'printf "%s %s %s\\n" "$1" "$2" "$3" >> "$GH_LOG"\n')
        proc = self._bash(self.step["run"], {
            "RUNNER_TEMP": str(runner_temp),
            "GITHUB_REPOSITORY": "example-adopter/skills-evals",
            "ISSUE_TITLE": "Scheduled propagation probes are failing",
            "RUN_URL": "https://example.com/run/1",
            "GATE_RESULT": gate,
            "ARMS_RESULT": arms_result,
            "GITHUB_EVENT_NAME": event,
            "PROBE_DRY_RUN": dry_run,
            "GH_OPEN_ISSUE": open_issue,
            "GH_LOG": str(gh_log),
        }, stub_dir)
        calls = (gh_log.read_text(encoding="utf-8").splitlines()
                 if gh_log.is_file() else [])
        failure = runner_temp / "propagation-failure.md"
        recovered = runner_temp / "propagation-recovered.md"
        # Vacuity control: the bodies are written by the first two statements
        # of the script, so their absence means the run under test stopped
        # before it decided anything and every assertion below is about nothing.
        self.assertTrue(failure.is_file() and recovered.is_file(),
                        "the step's script wrote no body files, so it did not "
                        "run far enough to have taken any branch")
        return (calls, proc.stdout,
                failure.read_text(encoding="utf-8"),
                recovered.read_text(encoding="utf-8"))

    # ---- B1: the body reports which half failed, and diagnoses neither ----

    def test_the_body_carries_both_job_results(self):
        _, _, body, _ = self.run_step(gate="failure", arms_result="success")
        self.assertIn("gate: failure", body)
        self.assertIn("arms: success", body)
        # The other way round too, so this cannot pass on a body that hard-codes
        # the pair the first case happened to use.
        _, _, body, _ = self.run_step(gate="success", arms_result="failure",
                                      open_issue="7")
        self.assertIn("gate: success", body)
        self.assertIn("arms: failure", body)

    def test_a_red_gate_is_diagnosed_as_a_fault_in_this_repository(self):
        """The old misdiagnosis, inverted.

        Before 2026-09-28 a red `gate` usually meant the Tier-3 account-store
        audit had drifted — a fault no commit here could cause or repair. That
        check is gone: `gate` is the hermetic mutation suite alone, so a red
        gate is now always a bug in this repository's own propagation harness,
        and the body has to say so rather than pointing anywhere else.
        """
        _, _, body, _ = self.run_step(gate="failure", arms_result="success")
        self.assertIn("fault in this repository", body)
        self.assertIn("test/test_propagation.py", body)
        # The registry still gets named — it is the right answer for a red ARM,
        # and must not disappear along with the gate's old diagnosis.
        self.assertIn("acsi-agentskills", body)

    # ---- B2: which write each pair of results reaches ----

    def test_a_red_run_with_no_open_issue_opens_one(self):
        calls, _, _, _ = self.run_step(gate="failure", arms_result="success")
        self.assertEqual(calls, ["issue create --repo"])

    def test_a_red_run_comments_on_the_issue_already_open(self):
        calls, _, _, _ = self.run_step(gate="failure", arms_result="failure",
                                       open_issue="41")
        self.assertEqual(calls, ["issue comment 41"])

    def test_a_green_run_closes_the_issue_that_is_open(self):
        # The defect this whole path exists for: before it, a repaired probe
        # left its tracking issue open until a human noticed.
        calls, _, _, recovered = self.run_step(gate="success",
                                               arms_result="success",
                                               open_issue="41")
        self.assertEqual(calls, ["issue close 41"])
        self.assertIn("green again", recovered)

    def test_a_green_run_with_nothing_open_writes_nothing(self):
        calls, out, _, _ = self.run_step(gate="success", arms_result="success")
        self.assertEqual(calls, [])
        self.assertIn("no tracking issue is open", out)

    def test_a_green_dispatch_never_closes_the_issue_the_schedule_filed(self):
        """The close is fenced to the event guaranteed to be testing `main`.

        `schedule` only ever runs against the default branch; a
        `workflow_dispatch` can run against any ref. A green dispatch from a
        branch whose fix has not merged yet would — before this guard —
        retract a finding the 05:41 run had filed on the still-broken `main`,
        and because the dedupe lookup is `--state open`, the next morning's
        schedule would file a BRAND-NEW issue instead of finding the closed
        one: the comment history the close path was added to preserve,
        stranded.

        Driven through the real script rather than read off its shape, because
        a guard can be written correctly and compare the wrong variable —
        which is the same reason `test_a_dry_run_reaches_the_lookup_and_writes_
        nothing` executes rather than greps.
        """
        calls, out, _, _ = self.run_step(gate="success", arms_result="success",
                                         open_issue="51",
                                         event="workflow_dispatch")
        self.assertEqual(
            calls, [],
            "a green workflow_dispatch must write NOTHING: it is not "
            "guaranteed to be testing `main`, so it has not shown the "
            "problem that opened the issue is fixed there")
        self.assertIn("only a scheduled run may close", out)
        # And the schedule still does close, so the guard cannot pass by
        # fencing the close off from every event including its own.
        calls, _, _, _ = self.run_step(gate="success", arms_result="success",
                                       open_issue="51", event="schedule")
        self.assertEqual(calls, ["issue close 51"])

    def test_a_red_dispatch_still_files_and_still_comments(self):
        # The fence is on the RETRACTION only. A red run found something real
        # regardless of which ref it targeted, so a dispatch that goes red
        # must still be able to say so — fencing the whole job to `schedule`
        # would be the over-correction that makes `dry_run=false` a knob
        # wired to nothing.
        calls, _, _, _ = self.run_step(gate="failure", arms_result="success",
                                       event="workflow_dispatch")
        self.assertEqual(calls, ["issue create --repo"])
        calls, _, _, _ = self.run_step(gate="failure", arms_result="success",
                                       open_issue="51",
                                       event="workflow_dispatch")
        self.assertEqual(calls, ["issue comment 51"])

    def test_a_half_green_run_is_not_a_close(self):
        # `success() || failure()` schedules this job whenever ANY need failed,
        # so both results have to be green before the close call is reached.
        # Testing only the both-green case would pass against a script that
        # closed on `gate` alone and threw away every red arm's report.
        for gate, arms_result in (("success", "failure"), ("failure", "success")):
            with self.subTest(gate=gate, arms=arms_result):
                calls, _, _, _ = self.run_step(gate=gate, arms_result=arms_result,
                                               open_issue="41")
                self.assertEqual(calls, ["issue comment 41"])

    def test_a_dry_run_reaches_the_lookup_and_writes_nothing(self):
        """The dry-run bail-out from the behavioural side.

        Its sibling in `DispatchAndDryRunTests` proves no write STATEMENT sits
        above the bail-out. This proves the flag is actually consulted: a
        script that built the guard correctly and compared the wrong variable
        would satisfy the shape test and write on every dispatch.
        """
        # On the dispatch, because that is the only event a dry run can happen
        # on: `inputs` is empty everywhere else, so PROBE_DRY_RUN renders as ''
        # on the schedule and the literal "true" can only come from the button.
        calls, out, _, _ = self.run_step(gate="failure", arms_result="success",
                                         open_issue="41", dry_run="true",
                                         event="workflow_dispatch")
        self.assertEqual(calls, [])
        # Detection still ran — the dry run prints what the lookup found, which
        # is the half of this step that has ever been wrong. The event is in
        # that line too, now that it is one of the facts deciding the outcome:
        # a reader of a dry run should not have to know the close is
        # schedule-only to work out that this one would not have closed.
        self.assertIn("open-issue=41", out)
        self.assertIn("gate=failure", out)
        self.assertIn("event=workflow_dispatch", out)


class PublishMessageAndPushTriggerTests(unittest.TestCase):
    """ROUTINE.md's publish message and the workflow set must not contradict.

    Step 4 of the Routine prompt mandates the commit message
    `propagation: account audit [skip ci]`, and `[skip ci]` is GitHub's
    documented instruction NOT to create a workflow run for a `push` event. So
    a workflow that tries to react to the Routine's publish with

        on:
          push:
            branches: [persistent/eval-results]

    cannot fire on a single one of those pushes. It is not red, not slow, and
    not logged anywhere — it simply never runs, which is the worst shape a CI
    dependency can take. That trap is exactly what a design note in ROUTINE.md
    ("A second route the issue does not consider") records, and prose is not an
    assertion: this pins the pair so the two halves cannot be edited apart.

    The coupling is DERIVED at both ends rather than hard-coded. The message is
    read out of ROUTINE.md's own step 4 (so rewording it is followed, not
    broken) and the listeners are read by parsing every workflow with a real
    YAML parser (never a line scan — a bare `on:` is the YAML 1.1 boolean True,
    which a regex reads straight past). What is asserted is the implication:
    if any workflow listens for a push on `persistent/eval-results`, the mandated message
    may not carry a CI-skip token. Removing the token is a legitimate decision
    — it is what stops a results-branch publish feeding CI back into itself, so
    it has consequences of its own — and this test does not forbid it; it
    forbids having it both ways silently.

    `test_the_detector_sees_a_listener_when_there_is_one` is the reason the
    implication is not vacuous today. No workflow here listens on
    `persistent/eval-results`, so the guard would pass against a detector that finds
    nothing ever; the positive control runs the same function over a synthetic
    document that does listen, and requires it to be found.
    """

    ROUTINE = EVAL_DIR / "ROUTINE.md"
    WORKFLOWS = REPO_ROOT / ".github" / "workflows"
    RESULTS_BRANCH = "persistent/eval-results"
    # GitHub's documented commit-message skip tokens. `skip-checks: true` is a
    # trailer rather than a message token and is deliberately not modelled.
    SKIP_TOKENS = ("[skip ci]", "[ci skip]", "[no ci]", "[skip actions]",
                   "[actions skip]")
    # Lexical on purpose, and legitimately so: this extracts the CONTENT of one
    # leaf token — the inline-code span on the "Commit message:" line — not the
    # structure of anything. The structural half of this test (which events a
    # workflow declares) goes through yaml.safe_load below.
    MESSAGE_RE = re.compile(r"Commit message:\s*`([^`]+)`")

    def _mandated_message(self) -> str:
        found = self.MESSAGE_RE.findall(
            self.ROUTINE.read_text(encoding="utf-8"))
        self.assertEqual(
            len(found), 1,
            "ROUTINE.md must declare the publish commit message exactly once, "
            "as an inline-code span on a `Commit message:` line — that "
            "declaration is what this guard reads. Found: "
            f"{found!r}")
        return found[0]

    @classmethod
    def _listens_on(cls, doc: dict, branch: str) -> bool:
        """Does this parsed workflow raise a run on a push to `branch`?

        Errs toward YES on anything it cannot resolve exactly: an unmatched
        pattern here reds this test and sends someone to look, whereas a missed
        one is the silent never-fires failure the whole guard exists to catch.
        """
        # A bare `on:` key parses as the YAML 1.1 boolean True, not "on".
        triggers = doc.get("on", doc.get(True)) if isinstance(doc, dict) else None
        # `on: push` and `on: [push]` are the two shorthand spellings, and both
        # mean EVERY push on EVERY branch — `persistent/eval-results` included. Neither
        # parses to a mapping (`{True: 'push'}` and `{True: ['push']}`
        # respectively), so a mapping-only reader returns False on the exact
        # shapes this guard exists to catch, which is the silent never-fires
        # failure one level up.
        if isinstance(triggers, str):
            return triggers == "push"
        if isinstance(triggers, list):
            return any(str(event) == "push" for event in triggers)
        if not isinstance(triggers, dict):
            # No `on:` at all, or a shape this cannot read. Unresolvable, so
            # say yes and send someone to look — per the docstring above.
            return True
        if "push" not in triggers:
            return False
        push = triggers["push"]
        if not isinstance(push, dict):
            return True  # bare `push:` — every branch, this one included
        if "branches" in push:
            return any(fnmatch.fnmatch(branch, str(pattern))
                       for pattern in push["branches"] or [])
        if "branches-ignore" in push:
            return not any(fnmatch.fnmatch(branch, str(pattern))
                           for pattern in push["branches-ignore"] or [])
        return True  # `push:` with only `paths:` — still every branch

    def _listeners(self) -> list[str]:
        import yaml
        # GitHub Actions reads BOTH extensions, so a `.yaml` workflow is a real
        # workflow that a `*.yml`-only glob never opens.
        paths = sorted(set(self.WORKFLOWS.glob("*.yml"))
                       | set(self.WORKFLOWS.glob("*.yaml")))
        self.assertTrue(
            paths,
            f"no workflows parsed out of {self.WORKFLOWS} — this guard would "
            "pass by finding nothing, which is not the same as agreeing")
        return [path.name for path in paths
                if self._listens_on(
                    yaml.safe_load(path.read_text(encoding="utf-8")),
                    self.RESULTS_BRANCH)]

    def test_the_detector_sees_a_listener_when_there_is_one(self):
        import yaml
        positive = yaml.safe_load(
            "on:\n  push:\n    branches: [persistent/eval-results]\n")
        self.assertTrue(
            self._listens_on(positive, self.RESULTS_BRANCH),
            "the listener detector must find the shape the design note warns "
            "about, or the guard below passes for the wrong reason")
        negative = yaml.safe_load("on:\n  push:\n    branches: [main]\n")
        self.assertFalse(
            self._listens_on(negative, self.RESULTS_BRANCH),
            "a push pinned to main is not a listener on the results branch")

    def test_the_detector_sees_the_bare_list_shorthand(self):
        """`on: [push]` means every push on every branch, this one included."""
        import yaml
        positive = yaml.safe_load("on: [push]\n")
        self.assertEqual(
            positive, {True: ["push"]},
            "this spelling parses to a LIST under the boolean-True key, not a "
            "mapping — that is why a mapping-only reader misses it")
        self.assertTrue(
            self._listens_on(positive, self.RESULTS_BRANCH),
            "`on: [push]` is an unfiltered push trigger, so it fires on "
            f"{self.RESULTS_BRANCH!r} like every other branch; a reader that "
            "returns False here would let the `[skip ci]` trap through in the "
            "one shape nothing else catches")
        # Negative control: same shorthand, no push event. Without this a
        # detector hard-wired to `return True` would satisfy the assertion
        # above and detect nothing at all.
        negative = yaml.safe_load("on: [pull_request, workflow_dispatch]\n")
        self.assertEqual(negative, {True: ["pull_request", "workflow_dispatch"]})
        self.assertFalse(
            self._listens_on(negative, self.RESULTS_BRANCH),
            "the list shorthand without `push` is not a push listener — this "
            "control is what proves the case above discriminates")

    def test_the_detector_sees_the_bare_scalar_shorthand(self):
        """`on: push` is the same trap one step smaller."""
        import yaml
        positive = yaml.safe_load("on: push\n")
        self.assertEqual(
            positive, {True: "push"},
            "this spelling parses to a STRING under the boolean-True key")
        self.assertTrue(
            self._listens_on(positive, self.RESULTS_BRANCH),
            "`on: push` is an unfiltered push trigger and fires on "
            f"{self.RESULTS_BRANCH!r}")
        negative = yaml.safe_load("on: workflow_dispatch\n")
        self.assertEqual(negative, {True: "workflow_dispatch"})
        self.assertFalse(
            self._listens_on(negative, self.RESULTS_BRANCH),
            "a scalar naming some other event is not a push listener — the "
            "control that keeps the case above from passing vacuously")

    def test_an_unreadable_trigger_block_errs_toward_yes(self):
        """The docstring's promise, asserted rather than described.

        A shape this cannot resolve must red the guard and send someone to
        look. The alternative — quietly answering "not a listener" — is the
        same silent miss as the two shorthands above.
        """
        self.assertTrue(
            self._listens_on({"jobs": {}}, self.RESULTS_BRANCH),
            "a document with no `on:` key at all is unresolvable, not a "
            "resolved no")
        self.assertTrue(
            self._listens_on(None, self.RESULTS_BRANCH),
            "an empty workflow file parses to None; that is unresolvable too")
        # Negative control: a trigger block this CAN resolve must still resolve
        # to no, or "errs toward yes" has degenerated into "always yes".
        self.assertFalse(
            self._listens_on({True: {"schedule": [{"cron": "0 5 * * *"}]}},
                             self.RESULTS_BRANCH),
            "a readable mapping with no `push` key is a resolved no, and must "
            "not be swept up by the unresolvable fallback")

    def test_a_dot_yaml_workflow_is_read_too(self):
        """`.yaml` is a workflow extension GitHub honours; the glob must too.

        A listener written `.yaml` evaded the guard entirely — not by parsing
        wrong but by never being opened, which leaves no trace at all.
        """
        with tempfile.TemporaryDirectory() as tmp:
            workflows = Path(tmp)
            (workflows / "listener.yaml").write_text(
                "on:\n  push:\n    branches: [persistent/eval-results]\njobs: {}\n",
                encoding="utf-8")
            with mock.patch.object(type(self), "WORKFLOWS", workflows):
                self.assertEqual(
                    self._listeners(), ["listener.yaml"],
                    "a `.yaml` workflow that listens on the results branch "
                    "must be found; a `*.yml`-only glob reports an empty list "
                    "and the guard passes for the wrong reason")
            # Negative control: same extension, pinned to main. Proves the case
            # above found a LISTENER rather than merely finding a file.
            (workflows / "listener.yaml").write_text(
                "on:\n  push:\n    branches: [main]\njobs: {}\n",
                encoding="utf-8")
            with mock.patch.object(type(self), "WORKFLOWS", workflows):
                self.assertEqual(
                    self._listeners(), [],
                    "a `.yaml` workflow pinned to main is not a listener on "
                    f"{self.RESULTS_BRANCH!r}")

    def test_no_push_listener_while_the_publish_message_skips_ci(self):
        message = self._mandated_message()
        tokens = [token for token in self.SKIP_TOKENS
                  if token in message.lower()]
        listeners = self._listeners()
        self.assertFalse(
            tokens and listeners,
            f"{listeners} listen for a push on {self.RESULTS_BRANCH!r}, but "
            f"ROUTINE.md step 4 mandates the commit message {message!r}, which "
            f"carries {tokens} — GitHub will not create a workflow run for "
            "such a push, so those workflows never fire on a Routine publish "
            "and say nothing about it. Either drop the token from step 4 and "
            "the live Routine prompt together (it is what keeps a "
            "results-branch publish from feeding CI back into itself, so read "
            "the design note in ROUTINE.md first), or trigger on something "
            "`[skip ci]` does not gate.")


if __name__ == "__main__":
    unittest.main(verbosity=1)
