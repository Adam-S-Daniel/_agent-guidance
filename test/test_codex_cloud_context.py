import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKER = REPO_ROOT / "scripts" / "check-codex-cloud-context.py"
BEGIN = "<!-- BEGIN FLEET GUIDANCE (managed by _agent-guidance) — DO NOT EDIT -->"
END = "<!-- END FLEET GUIDANCE -->"


class CodexCloudContextCheckTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.payload_text = "# Fleet guidance\n\nSynthetic canary: SILVER-FINCH-130.\n"
        self.payload = self.root / "payload.md"
        self.payload.write_text(self.payload_text, encoding="utf-8")
        self.repo_text = (
            "# AGENTS.md\n\nManaged stub.\n\n"
            "## Repo-specific additions\n\n"
            "Synthetic repo rule through EOF.\n"
        )
        self.repo_agents = self.root / "AGENTS.md"
        self.repo_agents.write_text(self.repo_text, encoding="utf-8")

    def tearDown(self):
        self.tempdir.cleanup()

    def cloud_block(self, payload_text=None):
        payload = payload_text if payload_text is not None else self.payload_text
        raw = payload.encode()
        version = hashlib.sha256(raw).hexdigest()[:8]
        if not payload.endswith("\n"):
            payload += "\n"
        return (
            f"{BEGIN}\n"
            f"<!-- fleet-guidance-version: {version} -->\n"
            f"fleet-guidance: installed (v{version}, {len(raw)} bytes) — Codex Cloud setup and maintenance\n"
            f"{payload}"
            f"{END}\n"
        )

    def envelope(self, body=None):
        body = body if body is not None else self.cloud_block() + "\n" + self.repo_text
        return f"# AGENTS.md instructions for /workspace/example\n\n<INSTRUCTIONS>\n{body}</INSTRUCTIONS>"

    @staticmethod
    def raw_item(item):
        return {"method": "rawResponseItem/completed", "params": {"item": item}}

    def response(self, envelope=None, *, completed=True, before=None, after=None):
        events = [
            {"method": "thread/started", "params": {}},
            self.raw_item({"type": "message", "role": "developer", "content": []}),
            *(before or []),
            self.raw_item(
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": envelope or self.envelope()},
                        {"type": "input_text", "text": "<environment_context>synthetic</environment_context>"},
                    ],
                }
            ),
            self.raw_item(
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Diagnostic prompt"}],
                }
            ),
            self.raw_item({"type": "reasoning", "summary": []}),
            self.raw_item(
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Synthetic answer"}],
                }
            ),
            *(after or []),
        ]
        if completed:
            events.append({"method": "turn/completed", "params": {}})
        return {
            "current_assistant_turn": {
                "type": "assistant",
                "role": "assistant",
                "turn_status": "completed" if completed else "in_progress",
                "error": None,
                "thread_events": {"events": events},
                "output_items": [],
            }
        }

    def run_check(self, response, *, payload=None, repo_agents=None):
        saved = self.root / "task.json"
        saved.write_text(json.dumps(response), encoding="utf-8")
        return subprocess.run(
            [
                "python3",
                str(CHECKER),
                str(saved),
                str(payload or self.payload),
                str(repo_agents or self.repo_agents),
            ],
            cwd=REPO_ROOT,
            env=os.environ.copy(),
            text=True,
            capture_output=True,
            check=False,
        )

    def assert_failed_without_content(self, result):
        self.assertEqual(1, result.returncode)
        self.assertTrue(result.stdout.startswith("codex-cloud-context: FAIL — "), result.stdout)
        self.assertNotIn("SILVER-FINCH-130", result.stdout)
        self.assertEqual("", result.stderr)

    def test_accepts_exact_initial_instruction_context(self):
        result = self.run_check(self.response())

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertTrue(result.stdout.startswith("codex-cloud-context: PASS — "))
        self.assertEqual("", result.stderr)

    def test_accepts_real_tracked_payload_and_repo_heading_shape(self):
        payload = REPO_ROOT / "agents-md" / "base.md"
        repo_agents = REPO_ROOT / "AGENTS.md"
        payload_text = payload.read_bytes().decode("utf-8")
        repo_text = repo_agents.read_bytes().decode("utf-8")
        envelope = self.envelope(self.cloud_block(payload_text) + "\n" + repo_text)

        result = self.run_check(self.response(envelope), payload=payload, repo_agents=repo_agents)

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_payload_digest_uses_exact_crlf_bytes(self):
        payload = self.root / "crlf-payload.md"
        payload_text = "# Fleet guidance\r\n\r\nCRLF bytes stay exact.\r\n"
        payload.write_bytes(payload_text.encode())
        envelope = self.envelope(self.cloud_block(payload_text) + "\n" + self.repo_text)

        result = self.run_check(self.response(envelope), payload=payload)

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_rejects_missing_truncated_and_duplicate_payload(self):
        bodies = {
            "missing": self.repo_text,
            "truncated": self.cloud_block().replace("SILVER-FINCH-130", "SILVER") + self.repo_text,
            "duplicate": self.cloud_block() + self.cloud_block() + self.repo_text,
        }
        for name, body in bodies.items():
            with self.subTest(name=name):
                self.assert_failed_without_content(self.run_check(self.response(self.envelope(body))))

    def test_rejects_stub_examples_without_a_real_managed_block(self):
        stub = (
            "fleet-guidance: installed (v12345678, 12 bytes)\n"
            "fleet-guidance: DEGRADED — example\n"
            + self.repo_text
        )
        self.assert_failed_without_content(self.run_check(self.response(self.envelope(stub))))

    def test_rejects_wrong_persisted_verdict(self):
        body = self.cloud_block().replace("fleet-guidance: installed", "fleet-guidance: current") + self.repo_text
        self.assert_failed_without_content(self.run_check(self.response(self.envelope(body))))

    def test_rejects_missing_or_truncated_repo_additions(self):
        missing = self.envelope(self.cloud_block() + "# Repo stub only\n")
        truncated = self.envelope(self.cloud_block() + self.repo_text[:-5])
        for envelope in (missing, truncated):
            with self.subTest():
                self.assert_failed_without_content(self.run_check(self.response(envelope)))

    def test_ignores_model_echo_and_output_items(self):
        response = self.response(self.envelope(self.repo_text))
        response["current_assistant_turn"]["output_items"] = [
            {"type": "message", "role": "assistant", "content": [{"text": self.cloud_block()}]}
        ]
        response["current_assistant_turn"]["thread_events"]["events"][-2] = self.raw_item(
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": self.cloud_block()}],
            }
        )

        self.assert_failed_without_content(self.run_check(response))

    def test_ignores_developer_and_tool_injection(self):
        injected = [
            self.raw_item(
                {
                    "type": "message",
                    "role": "developer",
                    "content": [{"type": "input_text", "text": self.cloud_block()}],
                }
            )
        ]
        response = self.response(self.envelope(self.repo_text), before=injected)
        response["current_assistant_turn"]["thread_events"]["events"].insert(
            -1,
            self.raw_item({"type": "function_call_output", "output": self.cloud_block()}),
        )

        self.assert_failed_without_content(self.run_check(response))

    def test_rejects_tool_output_before_initial_envelope(self):
        before = [self.raw_item({"type": "function_call_output", "output": self.cloud_block()})]

        self.assert_failed_without_content(self.run_check(self.response(before=before)))

    def test_rejects_tool_role_message_before_initial_envelope(self):
        before = [
            self.raw_item(
                {
                    "type": "message",
                    "role": "tool",
                    "content": [{"type": "input_text", "text": self.cloud_block()}],
                }
            )
        ]

        self.assert_failed_without_content(self.run_check(self.response(before=before)))

    def test_rejects_duplicate_initial_instruction_envelopes(self):
        response = self.response()
        duplicate = self.raw_item(
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": self.envelope()}],
            }
        )
        response["current_assistant_turn"]["thread_events"]["events"].insert(4, duplicate)

        self.assert_failed_without_content(self.run_check(response))

    def test_rejects_two_instruction_envelopes_in_one_raw_message(self):
        response = self.response()
        user_item = response["current_assistant_turn"]["thread_events"]["events"][2]
        user_item["params"]["item"]["content"].insert(
            1, {"type": "input_text", "text": self.envelope()}
        )

        self.assert_failed_without_content(self.run_check(response))

    def test_rejects_absent_events_and_incomplete_turn(self):
        absent = self.response()
        absent["current_assistant_turn"]["thread_events"] = {"events": []}
        incomplete = self.response(completed=False)
        for response in (absent, incomplete):
            with self.subTest():
                self.assert_failed_without_content(self.run_check(response))

    def test_rejects_malformed_raw_event(self):
        malformed = self.response()
        malformed["current_assistant_turn"]["thread_events"]["events"][1] = {
            "method": "rawResponseItem/completed",
            "params": {},
        }

        self.assert_failed_without_content(self.run_check(malformed))


if __name__ == "__main__":
    unittest.main()
