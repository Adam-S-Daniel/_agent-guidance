"""Workload identity federation is optional, so the weekly roster refresh
must not fail without it.

roster-refresh.yml dispatches eval.yml with `roster_only` every week, and the
`roster` job mints an Anthropic token on every dispatch. With the four
ANTHROPIC_* federation variables unset, that mint used to fail the job every
week. Now the roster job's mint exits 0 with a ::notice:: and
`federation=off`, and "Refresh the model roster" publishes no roster and says
so. A partial or malformed set still fails, and the `eval` job's mint, which
an eval leg cannot do without, still fails on an unset variable.

The step scripts are taken from the parsed workflow and run under
`bash -e` (GitHub's default shell) with a fake `curl` first on PATH, so no
network is used. The ids are invented.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
EVAL_WORKFLOW = ROOT / ".github" / "workflows" / "eval.yml"
MINT = "Mint OIDC token and exchange for Anthropic access token"
REFRESH = "Refresh the model roster"

VALID = {
    "FEDERATION_RULE_ID": "fdrl_EXAMPLE0001",
    "ORGANIZATION_ID": "00000000-0000-0000-0000-000000000000",
    "SERVICE_ACCOUNT_ID": "svac_EXAMPLE0001",
    "WORKSPACE_ID": "wrkspc_EXAMPLE0001",
}
EMPTY = {k: "" for k in VALID}


def _step(job: str, name: str) -> dict:
    doc = yaml.safe_load(EVAL_WORKFLOW.read_text(encoding="utf-8"))
    return next(s for s in doc["jobs"][job]["steps"] if s.get("name") == name)


class _StepRunner(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="federation-"))
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(self.tmp)], check=False))
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.curl_log = self.tmp / "curl.log"
        fake = self.bin / "curl"
        fake.write_text(f'#!/bin/sh\necho called >> "{self.curl_log}"\nexit 22\n', encoding="utf-8")
        fake.chmod(0o755)
        self.output = self.tmp / "github_output"
        self.summary = self.tmp / "step_summary"
        self.runner_temp = self.tmp / "runner"
        self.runner_temp.mkdir()

    def run_step(self, script: str, env: dict) -> subprocess.CompletedProcess:
        full = {
            "PATH": f"{self.bin}{os.pathsep}/usr/bin{os.pathsep}/bin",
            "HOME": str(self.tmp),
            "GITHUB_OUTPUT": str(self.output),
            "GITHUB_STEP_SUMMARY": str(self.summary),
            "RUNNER_TEMP": str(self.runner_temp),
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "not-a-real-token",
            "ACTIONS_ID_TOKEN_REQUEST_URL": "https://token.example.com/?x=1",
            **env,
        }
        return subprocess.run(["bash", "-e", "-c", script], env=full, cwd=self.tmp,
                              capture_output=True, text=True, timeout=60)

    def curl_called(self) -> bool:
        return self.curl_log.exists()

    def outputs(self) -> str:
        return self.output.read_text(encoding="utf-8") if self.output.exists() else ""


class TestRosterMintIsOptional(_StepRunner):
    def setUp(self):
        super().setUp()
        self.step = _step("roster", MINT)

    def test_the_step_has_the_id_the_refresh_step_reads(self):
        self.assertEqual(self.step.get("id"), "federation")
        refresh = _step("roster", REFRESH)
        self.assertEqual(refresh["env"]["FEDERATION"], "${{ steps.federation.outputs.federation }}")

    def test_all_four_unset_skips_with_a_notice(self):
        proc = self.run_step(self.step["run"], EMPTY)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("::notice::Anthropic workload identity federation is not configured", proc.stdout)
        self.assertIn("federation=off", self.outputs())
        self.assertFalse(self.curl_called())
        self.assertFalse((self.runner_temp / "anthropic-bearer").exists())

    def test_a_partial_set_still_fails(self):
        for name in VALID:
            with self.subTest(only=name):
                self.output.unlink(missing_ok=True)
                proc = self.run_step(self.step["run"], {**EMPTY, name: VALID[name]})
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn("::error::repository variable ANTHROPIC_", proc.stdout)
                self.assertNotIn("federation=off", self.outputs())
                self.assertFalse(self.curl_called())

    def test_a_malformed_value_still_fails(self):
        for name, bad in (("FEDERATION_RULE_ID", "fdrl_x"), ("ORGANIZATION_ID", "not-a-uuid"),
                          ("SERVICE_ACCOUNT_ID", "svac_$(id)"), ("WORKSPACE_ID", "workspace")):
            with self.subTest(variable=name):
                proc = self.run_step(self.step["run"], {**VALID, name: bad})
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn(f"::error::repository variable ANTHROPIC_{name}", proc.stdout)
                self.assertFalse(self.curl_called())

    def test_a_full_valid_set_goes_on_to_the_exchange(self):
        proc = self.run_step(self.step["run"], VALID)
        # The fake curl fails the OIDC request, so the step fails after the checks.
        self.assertNotEqual(proc.returncode, 0)
        self.assertTrue(self.curl_called())
        self.assertNotIn("federation=off", self.outputs())

    def test_the_eval_jobs_mint_still_fails_when_unset(self):
        step = _step("eval", MINT)
        self.assertNotIn("federation=off", step["run"])
        proc = self.run_step(step["run"], EMPTY)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("::error::repository variable ANTHROPIC_FEDERATION_RULE_ID is unset", proc.stdout)
        self.assertFalse(self.curl_called())


class TestRefreshSkipsWithoutFederation(_StepRunner):
    def test_off_publishes_no_roster_and_says_so(self):
        step = _step("roster", REFRESH)
        proc = self.run_step(step["run"], {"FEDERATION": "off"})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("::notice::model roster not refreshed", proc.stdout)
        self.assertIn("The roster was not refreshed this run", self.summary.read_text(encoding="utf-8"))
        self.assertFalse((self.runner_temp / "roster" / "latest.json").exists())

    def test_the_skip_sits_before_any_work(self):
        script = _step("roster", REFRESH)["run"]
        self.assertLess(script.index('if [ "${FEDERATION:-}" = off ]; then'), script.index("refresh() {"))


if __name__ == "__main__":
    unittest.main()
