import hashlib
import os
import pwd
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK = REPO_ROOT / ".claude" / "hooks" / "fleet-memory.sh"
BEGIN = "<!-- BEGIN FLEET GUIDANCE (managed by _agent-guidance) — DO NOT EDIT -->"
END = "<!-- END FLEET GUIDANCE -->"


class CodexCloudDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.claude_config = self.root / "claude-config"
        self.tmpdir_path = self.root / "tmp"
        self.tmpdir_path.mkdir()
        self.codex_home = self.root / "opt" / "codex"
        self.payload = self.root / "fleet-guidance.md"
        self.payload.write_text("# Fleet guidance\n\nCloud canary: ORANGE-TERN-130.\n", encoding="utf-8")
        self.repo_agents_before = (REPO_ROOT / "AGENTS.md").read_bytes()

    def tearDown(self):
        self.assertEqual(self.repo_agents_before, (REPO_ROOT / "AGENTS.md").read_bytes())
        self.tempdir.cleanup()

    def run_cloud(
        self,
        *,
        codex_home=None,
        payload=None,
        skip=None,
        args=None,
        preexec_fn=None,
        set_codex_home=True,
    ):
        env = os.environ.copy()
        env.update(
            HOME=str(self.home),
            CLAUDE_CONFIG_DIR=str(self.claude_config),
            FLEET_GUIDANCE_PAYLOAD=str(payload or self.payload),
            TMPDIR=str(self.tmpdir_path),
        )
        if set_codex_home:
            env["CODEX_HOME"] = str(codex_home or self.codex_home)
        else:
            env.pop("CODEX_HOME", None)
        if skip is None:
            env.pop("FLEET_GUIDANCE_SKIP", None)
        else:
            env["FLEET_GUIDANCE_SKIP"] = skip
        result = subprocess.run(
            ["bash", str(HOOK), *(args or ["--codex-cloud"])],
            cwd=REPO_ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            preexec_fn=preexec_fn,
        )
        self.assertEqual([], list(self.tmpdir_path.iterdir()), "Cloud mode wrote outside its target")
        self.assertEqual(
            [],
            list(self.root.rglob(".fleet-guidance-*")),
            "Cloud mode leaked an atomic-write temporary file",
        )
        return result

    def expected_installed_block(self, payload=None):
        payload = payload or self.payload
        raw = payload.read_bytes()
        version = hashlib.sha256(raw).hexdigest()[:8]
        body = raw.decode("utf-8")
        return (
            f"{BEGIN}\n"
            f"<!-- fleet-guidance-version: {version} -->\n"
            f"fleet-guidance: installed (v{version}, {len(raw)} bytes) — Codex Cloud setup and maintenance\n"
            f"{body}"
            f"{END}\n"
        )

    def assert_one_degraded_line(self, result):
        self.assertNotEqual(0, result.returncode)
        lines = result.stdout.splitlines()
        self.assertEqual(1, len(lines), result.stdout)
        self.assertTrue(lines[0].startswith("fleet-guidance: DEGRADED — "), lines[0])

    def test_fresh_cloud_install_creates_only_codex_global_instructions(self):
        result = self.run_cloud()

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("", result.stderr)
        self.assertEqual(self.expected_installed_block(), (self.codex_home / "AGENTS.md").read_text(encoding="utf-8"))
        self.assertEqual(1, (self.codex_home / "AGENTS.md").read_text(encoding="utf-8").count("ORANGE-TERN-130"))
        self.assertEqual(1, (self.codex_home / "AGENTS.md").read_text(encoding="utf-8").count("fleet-guidance:"))
        self.assertFalse(self.claude_config.exists())
        self.assertIn("fleet-guidance: installed", result.stdout)
        self.assertEqual(1, len(result.stdout.splitlines()))

    def test_default_codex_home_is_created(self):
        result = self.run_cloud(set_codex_home=False)

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertTrue((self.home / ".codex" / "AGENTS.md").is_file())

    def test_operator_content_survives_and_repeat_is_byte_idempotent(self):
        self.codex_home.mkdir(parents=True)
        destination = self.codex_home / "AGENTS.md"
        operator_content = b"# Operator instructions\n\nKeep these exact bytes.\n"
        destination.write_bytes(operator_content)

        first = self.run_cloud()
        after_first = destination.read_bytes()
        second = self.run_cloud()

        self.assertEqual(0, first.returncode, first.stdout + first.stderr)
        self.assertEqual(0, second.returncode, second.stdout + second.stderr)
        self.assertEqual(
            operator_content + self.expected_installed_block().encode(),
            after_first,
        )
        self.assertEqual(after_first, destination.read_bytes())
        self.assertIn("fleet-guidance: current", second.stdout)

    def test_refresh_preserves_content_around_block_byte_for_byte(self):
        self.codex_home.mkdir(parents=True)
        destination = self.codex_home / "AGENTS.md"
        installed = self.expected_installed_block().encode()
        replacement = self.root / "byte-refresh.md"
        replacement.write_text("# Replacement\n\nNew bytes.\n", encoding="utf-8")
        refreshed = self.expected_installed_block(replacement).encode()
        cases = {
            "suffix_without_final_newline": (b"prefix without newline", b"suffix without newline"),
            "multiple_trailing_blank_lines": (b"prefix\n\n", b"suffix\n\n\n"),
        }
        for name, (prefix, suffix) in cases.items():
            with self.subTest(name=name):
                destination.write_bytes(prefix + b"\n" + installed + suffix)

                result = self.run_cloud(payload=replacement)

                self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                self.assertEqual(prefix + b"\n" + refreshed + suffix, destination.read_bytes())

    def test_refresh_replaces_payload_and_persisted_verdict(self):
        self.assertEqual(0, self.run_cloud().returncode)
        replacement = self.root / "replacement.md"
        replacement.write_text("# Refreshed\n\nCloud canary: BLUE-WREN-131.\n", encoding="utf-8")

        result = self.run_cloud(payload=replacement)
        content = (self.codex_home / "AGENTS.md").read_text(encoding="utf-8")

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(self.expected_installed_block(replacement), content)
        self.assertNotIn("ORANGE-TERN-130", content)
        self.assertEqual(1, content.count("fleet-guidance:"))

    def test_skip_persists_model_visible_verdict_and_reenable_restores_payload(self):
        self.codex_home.mkdir(parents=True)
        destination = self.codex_home / "AGENTS.md"
        operator_content = "# Operator instructions\n"
        destination.write_text(operator_content, encoding="utf-8")
        self.assertEqual(0, self.run_cloud().returncode)

        skipped = self.run_cloud(skip="1")
        skipped_content = destination.read_text(encoding="utf-8")

        self.assertEqual(0, skipped.returncode, skipped.stdout + skipped.stderr)
        self.assertTrue(skipped_content.startswith(operator_content))
        self.assertNotIn("ORANGE-TERN-130", skipped_content)
        self.assertEqual(1, skipped_content.count("fleet-guidance:"))
        self.assertIn("fleet-guidance: skipped (FLEET_GUIDANCE_SKIP set)", skipped_content)
        skipped_bytes = destination.read_bytes()
        self.assertEqual(0, self.run_cloud(skip="1").returncode)
        self.assertEqual(skipped_bytes, destination.read_bytes())

        reenabled = self.run_cloud(skip="0")

        self.assertEqual(0, reenabled.returncode, reenabled.stdout + reenabled.stderr)
        self.assertEqual(operator_content + self.expected_installed_block(), destination.read_text(encoding="utf-8"))

    def test_nonempty_override_takes_precedence(self):
        self.codex_home.mkdir(parents=True)
        agents = self.codex_home / "AGENTS.md"
        override = self.codex_home / "AGENTS.override.md"
        agents.write_text("AGENTS sentinel\n", encoding="utf-8")
        override.write_text("Override sentinel\n", encoding="utf-8")

        result = self.run_cloud()

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual("AGENTS sentinel\n", agents.read_text(encoding="utf-8"))
        self.assertIn("Override sentinel\n", override.read_text(encoding="utf-8"))
        self.assertIn("ORANGE-TERN-130", override.read_text(encoding="utf-8"))

    def test_empty_override_falls_back_to_agents(self):
        self.codex_home.mkdir(parents=True)
        override = self.codex_home / "AGENTS.override.md"
        override.write_bytes(b"")

        result = self.run_cloud()

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(b"", override.read_bytes())
        self.assertIn("ORANGE-TERN-130", (self.codex_home / "AGENTS.md").read_text(encoding="utf-8"))

    def test_invalid_override_is_refused_instead_of_falling_back(self):
        for kind in ("directory", "unreadable"):
            with self.subTest(kind=kind):
                home = self.root / f"override-{kind}"
                home.mkdir()
                agents = home / "AGENTS.md"
                agents.write_text("AGENTS sentinel\n", encoding="utf-8")
                override = home / "AGENTS.override.md"
                preexec_fn = None
                if kind == "directory":
                    override.mkdir()
                else:
                    override.write_text("OVERRIDE MUST SURVIVE\n", encoding="utf-8")
                    override.chmod(0o200)
                    if os.geteuid() == 0:
                        nobody = pwd.getpwnam("nobody")
                        self.root.chmod(0o755)
                        home.chmod(0o755)
                        self.payload.chmod(0o644)

                        def demote():
                            os.setgid(nobody.pw_gid)
                            os.setuid(nobody.pw_uid)

                        preexec_fn = demote
                try:
                    result = self.run_cloud(codex_home=home, preexec_fn=preexec_fn)
                finally:
                    if kind == "unreadable":
                        override.chmod(0o600)

                self.assert_one_degraded_line(result)
                self.assertEqual("AGENTS sentinel\n", agents.read_text(encoding="utf-8"))

    def test_payload_cannot_collide_with_persisted_verdict(self):
        collision = self.root / "collision.md"
        collision.write_text("# Guidance\nfleet-guidance: current example\n", encoding="utf-8")

        result = self.run_cloud(payload=collision)

        self.assert_one_degraded_line(result)
        self.assertFalse(self.codex_home.exists())

    def test_malformed_managed_markers_are_refused_without_overwrite(self):
        malformed = {
            "begin_only": f"operator\n{BEGIN}\nold payload\n",
            "end_only": f"operator\n{END}\n",
            "duplicate": f"{BEGIN}\none\n{END}\n{BEGIN}\ntwo\n{END}\n",
            "reversed": f"{END}\n{BEGIN}\n",
        }
        for name, content in malformed.items():
            with self.subTest(name=name):
                home = self.root / name
                home.mkdir()
                destination = home / "AGENTS.md"
                before = content.encode()
                destination.write_bytes(before)

                result = self.run_cloud(codex_home=home)

                self.assert_one_degraded_line(result)
                self.assertEqual(before, destination.read_bytes())

    def test_missing_and_empty_payloads_fail_visibly(self):
        empty = self.root / "empty.md"
        empty.write_bytes(b"")
        for name, payload in (("missing", self.root / "missing.md"), ("empty", empty)):
            with self.subTest(name=name):
                result = self.run_cloud(codex_home=self.root / f"codex-{name}", payload=payload)
                self.assert_one_degraded_line(result)

    def test_unwritable_destination_is_refused(self):
        blocked = self.root / "blocked"
        blocked.mkdir()
        blocked.chmod(0o555)
        preexec_fn = None
        if os.geteuid() == 0:
            nobody = pwd.getpwnam("nobody")
            self.root.chmod(0o755)
            self.payload.chmod(0o644)

            def demote():
                os.setgid(nobody.pw_gid)
                os.setuid(nobody.pw_uid)

            preexec_fn = demote
        try:
            result = self.run_cloud(codex_home=blocked, preexec_fn=preexec_fn)
        finally:
            blocked.chmod(0o755)

        self.assert_one_degraded_line(result)
        self.assertFalse((blocked / "AGENTS.md").exists())

    def test_unreadable_destination_is_refused_without_overwrite(self):
        home = self.root / "unreadable"
        home.mkdir()
        destination = home / "AGENTS.md"
        before = b"OPERATOR CONTENT THAT MUST SURVIVE\n"
        destination.write_bytes(before)
        destination.chmod(0o200)
        preexec_fn = None
        if os.geteuid() == 0:
            nobody = pwd.getpwnam("nobody")
            self.root.chmod(0o755)
            home.chmod(0o755)
            self.payload.chmod(0o644)

            def demote():
                os.setgid(nobody.pw_gid)
                os.setuid(nobody.pw_uid)

            preexec_fn = demote
        try:
            result = self.run_cloud(codex_home=home, preexec_fn=preexec_fn)
        finally:
            destination.chmod(0o600)

        self.assert_one_degraded_line(result)
        self.assertEqual(before, destination.read_bytes())

    def test_unknown_argument_fails_without_writing(self):
        result = self.run_cloud(args=["--codex-cloud", "--surprise"])

        self.assert_one_degraded_line(result)
        self.assertFalse(self.codex_home.exists())


if __name__ == "__main__":
    unittest.main()
