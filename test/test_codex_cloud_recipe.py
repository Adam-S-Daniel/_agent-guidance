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

    def test_recipe_runs_the_verified_download_in_codex_cloud_mode(self):
        text = DOC.read_text(encoding="utf-8")
        block = recipe_block()
        self.assertIn('bash "$tmp/fleet-memory.sh" --codex-cloud', block)
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
            env = dict(os.environ, PATH=f"{stubs}{os.pathsep}{os.environ['PATH']}",
                       HOME=str(root), CODEX_HOME=str(root / "codex"))
            result = subprocess.run(["bash", "-c", block], env=env, text=True,
                                    capture_output=True, timeout=60)
            self.assertNotEqual(0, result.returncode)
            self.assertFalse(ran.exists())
            self.assertFalse((root / "codex").exists())


if __name__ == "__main__":
    unittest.main()
