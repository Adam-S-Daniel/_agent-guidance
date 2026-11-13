import hashlib
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DOC = REPO_ROOT / "docs" / "codex-cloud.md"
HOOK_REL = ".claude/hooks/fleet-memory.sh"


def recipe_block():
    """The one bash block in docs/codex-cloud.md that carries the pin."""
    blocks = re.findall(r"^```bash\n(.*?)^```$", DOC.read_text(encoding="utf-8"), re.S | re.M)
    pinned = [b for b in blocks if "fleet_pin_commit=" in b]
    assert len(pinned) == 1, "expected exactly one block with the pin"
    return pinned[0]


class CodexCloudRecipeTests(unittest.TestCase):
    def test_exactly_one_well_formed_pin_pair(self):
        block = recipe_block()
        self.assertEqual(1, len(re.findall(r"^fleet_pin_commit=[0-9a-f]{40}$", block, re.M)))
        self.assertEqual(1, len(re.findall(r"^fleet_pin_sha256=[0-9a-f]{64}$", block, re.M)))
        self.assertEqual(1, len(re.findall(r"\b[0-9a-f]{40}\b", block)))
        self.assertEqual(1, len(re.findall(r"\b[0-9a-f]{64}\b", block)))

    def test_pin_matches_the_file_at_the_pinned_commit_when_it_is_in_history(self):
        block = recipe_block()
        commit = re.search(r"^fleet_pin_commit=([0-9a-f]{40})$", block, re.M).group(1)
        digest = re.search(r"^fleet_pin_sha256=([0-9a-f]{64})$", block, re.M).group(1)
        shown = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "show", f"{commit}:{HOOK_REL}"],
            capture_output=True,
        )
        if shown.returncode != 0:
            self.skipTest("the pinned commit is not in this clone's history")
        self.assertEqual(digest, hashlib.sha256(shown.stdout).hexdigest())

    def test_pin_is_the_working_tree_hook_digest(self):
        # A change to fleet-memory.sh must bump the pin in the same PR.
        digest = re.search(r"^fleet_pin_sha256=([0-9a-f]{64})$", recipe_block(), re.M).group(1)
        self.assertEqual(
            digest, hashlib.sha256((REPO_ROOT / HOOK_REL).read_bytes()).hexdigest()
        )

    def test_the_function_is_defined_apart_from_its_call(self):
        text = DOC.read_text(encoding="utf-8")
        self.assertNotIn("\nfleet_guidance_install\n", recipe_block())
        self.assertIn("fleet_guidance_install || exit 1", text)

    def test_recipe_runs_the_verified_download_in_codex_cloud_mode(self):
        text = DOC.read_text(encoding="utf-8")
        block = recipe_block()
        self.assertIn('bash "$hook" --codex-cloud', block)
        self.assertIn("FLEET_GUIDANCE_PAYLOAD=", block)
        self.assertIn("FLEET_GUIDANCE_PAYLOAD_STAMP=", block)
        self.assertIn("sha256sum --check", block)
        # Nothing in the doc runs the checkout's own copy of the hook any more
        # (the one mention left is the historical probe).
        self.assertEqual(1, len(re.findall(r"^\s*bash \.claude/hooks/fleet-memory\.sh", text, re.M)))

    def test_a_digest_mismatch_stops_setup_and_never_runs_the_download(self):
        block = recipe_block()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stubs = root / "stubs"
            stubs.mkdir()
            ran = root / "ran"
            curl = stubs / "curl"
            curl.write_text(
                '#!/bin/bash\nwhile [ $# -gt 0 ]; do [ "$1" = -o ] && out=$2; shift; done\n'
                f"printf 'touch \"{ran}\"\\n' > \"$out\"\n",
                encoding="utf-8",
            )
            curl.chmod(0o755)
            git = stubs / "git"
            git.write_text("#!/bin/bash\nexit 1\n", encoding="utf-8")
            git.chmod(0o755)
            env = dict(os.environ, PATH=f"{stubs}{os.pathsep}{os.environ['PATH']}",
                       HOME=str(root), CODEX_HOME=str(root / "codex"))
            result = subprocess.run(
                ["bash", "-c", block + "\nfleet_guidance_install"],
                env=env, text=True, capture_output=True, timeout=60)
            self.assertNotEqual(0, result.returncode)
            self.assertFalse(ran.exists())
            self.assertFalse((root / "codex" / "AGENTS.md").exists())

    def test_success_path_offline_uses_the_stamp_and_codex_home(self):
        block = recipe_block()
        head = "cd" * 20
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stubs = root / "stubs"
            stubs.mkdir()
            served = root / "base.md"
            served.write_text(
                "# AGENTS.md\n\n> **Managed by [`_agent-guidance`].**\n\nSynthetic rule.\n",
                encoding="utf-8")
            (stubs / "curl").write_text(
                '#!/bin/bash\nwhile [ $# -gt 0 ]; do case "$1" in -o) out=$2;; http*) url=$1;; esac; shift; done\n'
                f'case "$url" in */fleet-memory.sh) cp "{REPO_ROOT / HOOK_REL}" "$out";;\n'
                f'*/base.md) cp "{served}" "$out";; *) exit 22;; esac\n', encoding="utf-8")
            (stubs / "git").write_text(
                '#!/bin/bash\ncase " $* " in *" log "*) echo "' + head + ' 1794606898";; esac\nexit 0\n',
                encoding="utf-8")
            for tool in stubs.iterdir():
                tool.chmod(0o755)
            env = dict(os.environ, PATH=f"{stubs}{os.pathsep}{os.environ['PATH']}",
                       HOME=str(root / "home"), CODEX_HOME=str(root / "codex"),
                       FLEET_GUIDANCE_RECEIPT="0")
            result = subprocess.run(
                ["bash", "-c", block + "\nfleet_guidance_install"],
                env=env, text=True, capture_output=True, timeout=60)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIn(f"fleet-guidance-source: _agent-guidance@{head}", result.stdout)
            text = (root / "codex" / "AGENTS.md").read_text(encoding="utf-8")
            self.assertIn("Synthetic rule.", text)
            pin = re.search(r"^fleet_pin_sha256=([0-9a-f]{64})$", block, re.M).group(1)
            self.assertTrue((root / "codex" / "fleet-guidance" / f"fleet-memory-{pin}.sh").is_file())


if __name__ == "__main__":
    unittest.main()
