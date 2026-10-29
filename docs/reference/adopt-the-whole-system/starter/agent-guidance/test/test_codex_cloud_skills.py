"""Synthetic evidence tests for the initial Cloud skill catalog."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts" / "check-codex-cloud-skills.py"


class CodexCloudSkillCheckTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.lock = self.root / "skills.lock"
        self.lock.write_text(json.dumps({"skills": {"bundle/alpha": "sha256:example"}}), encoding="utf-8")

    def tearDown(self):
        self.tempdir.cleanup()

    @staticmethod
    def raw(item):
        return {"method": "rawResponseItem/completed", "params": {"item": item}}

    @staticmethod
    def skill_block(rows):
        return (
            "<skills_instructions>\n## Skills\nA synthetic catalog.\n"
            "### Available skills\n" + "\n".join(rows) + "\n</skills_instructions>"
        )

    @staticmethod
    def row(name, path=None):
        path = path or f"/home/agent/.agents/skills/{name}/SKILL.md"
        return f"- {name}: Synthetic description. (file: {path})"

    def response(self, rows=None, *, developer=True, completed=True, echo=False, extra_events=None):
        rows = rows if rows is not None else [self.row("alpha")]
        events = [{"method": "thread/started", "params": {}}]
        if developer:
            events.append(self.raw({
                "type": "message", "role": "developer",
                "content": [{"type": "input_text", "text": self.skill_block(rows)}],
            }))
        events.append(self.raw({
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "Synthetic instruction"}],
        }))
        events.extend(extra_events or [])
        events.append(self.raw({
            "type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": self.skill_block(rows) if echo else "Done"}],
        }))
        if completed:
            events.append({"method": "turn/completed", "params": {}})
        return {"current_assistant_turn": {
            "type": "assistant", "role": "assistant",
            "turn_status": "completed" if completed else "in_progress",
            "error": None, "thread_events": {"events": events},
            "output_items": [{"type": "message", "content": [{"text": self.skill_block(rows)}]}]
        }}

    def run_check(self, response, expected=None):
        saved = self.root / "response.json"
        saved.write_text(json.dumps(response), encoding="utf-8")
        return subprocess.run(
            ["python3", str(CHECKER), str(saved), str(expected or self.lock)],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )

    def assert_failure(self, response, expected=None):
        result = self.run_check(response, expected)
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertTrue(result.stdout.startswith("codex-cloud-skills: FAIL — "))
        self.assertEqual("", result.stderr)
        self.assertNotIn("Synthetic description", result.stdout)

    def test_accepts_initial_developer_catalog_for_lock(self):
        rows = [self.row("openai-docs", "/opt/codex/skills/.system/openai-docs/SKILL.md"), self.row("alpha")]
        result = self.run_check(self.response(rows))
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("1 expected skills", result.stdout)

    def test_accepts_federated_source_skill_rows(self):
        self.lock.write_text(json.dumps({
            "skills": {"bundle/alpha": "sha256:example"},
            "sources": [{"skills": {"other/beta": "sha256:example"}}],
        }), encoding="utf-8")
        result = self.run_check(self.response([self.row("alpha"), self.row("beta")]))
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("2 expected skills", result.stdout)

    def test_manifest_requires_exact_actual_name_and_path(self):
        manifest = self.root / "expected.json"
        manifest.write_text(json.dumps({"skills": [{
            "name": "alpha", "file": "/home/agent/.agents/skills/alpha/SKILL.md"
        }]}), encoding="utf-8")
        self.assertEqual(0, self.run_check(self.response(), manifest).returncode)
        self.assert_failure(self.response([self.row("alpha", "/other/.agents/skills/alpha/SKILL.md")]), manifest)

    def test_manifest_can_match_frontmatter_alias_and_description(self):
        manifest = self.root / "expected.json"
        manifest.write_text(json.dumps({"skills": [{
            "name": "bundle:alpha", "file": "/home/agent/.agents/skills/alpha/SKILL.md",
            "description": "Synthetic description.",
        }]}), encoding="utf-8")
        row = self.row("bundle:alpha", "/home/agent/.agents/skills/alpha/SKILL.md")
        self.assertEqual(0, self.run_check(self.response([row]), manifest).returncode)
        self.assert_failure(self.response([row.replace("Synthetic description.", "Different.")]), manifest)

    def test_rejects_echo_only_in_assistant_and_output_items(self):
        self.assert_failure(self.response(developer=False, echo=True))

    def test_rejects_tool_output_before_catalog(self):
        tool = self.raw({"type": "function_call_output", "output": self.skill_block([self.row("alpha")])})
        response = self.response(extra_events=[tool])
        events = response["current_assistant_turn"]["thread_events"]["events"]
        events.insert(1, events.pop(3))
        self.assert_failure(response)

    def test_rejects_incomplete_failed_and_missing_completion(self):
        incomplete = self.response(completed=False)
        failed = self.response()
        failed["current_assistant_turn"]["error"] = {"message": "synthetic"}
        missing = self.response()
        missing["current_assistant_turn"]["thread_events"]["events"].pop()
        for response in (incomplete, failed, missing):
            with self.subTest():
                self.assert_failure(response)

    def test_rejects_missing_duplicate_and_truncated_catalog(self):
        missing = self.response(developer=False)
        duplicate = self.response()
        events = duplicate["current_assistant_turn"]["thread_events"]["events"]
        events.insert(2, events[1].copy())
        truncated = self.response()
        text = truncated["current_assistant_turn"]["thread_events"]["events"][1]["params"]["item"]["content"][0]
        text["text"] = text["text"].removesuffix("</skills_instructions>")
        for response in (missing, duplicate, truncated):
            with self.subTest():
                self.assert_failure(response)

    def test_rejects_missing_wrong_path_duplicate_and_extra_fleet_skills(self):
        cases = [
            [],
            [self.row("alpha", "/opt/codex/skills/alpha/SKILL.md")],
            [self.row("alpha"), self.row("alpha")],
            [self.row("alpha"), self.row("beta")],
        ]
        for rows in cases:
            with self.subTest(rows=rows):
                self.assert_failure(self.response(rows))

    def test_empty_manifest_allows_builtin_only_and_rejects_fleet_skill(self):
        manifest = self.root / "expected.json"
        manifest.write_text('{"skills": []}', encoding="utf-8")
        builtins = [self.row("imagegen", "/opt/codex/skills/.system/imagegen/SKILL.md")]
        result = self.run_check(self.response(builtins), manifest)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assert_failure(self.response(builtins + [self.row("alpha")]), manifest)

    def test_rejects_malformed_response_and_lock(self):
        bad_response = self.response()
        bad_response["current_assistant_turn"]["thread_events"]["events"][1]["params"] = {}
        self.assert_failure(bad_response)
        self.lock.write_text('{"skills": {"bundle/alpha": "x", "other/alpha": "y"}}', encoding="utf-8")
        self.assert_failure(self.response())


if __name__ == "__main__":
    unittest.main()
