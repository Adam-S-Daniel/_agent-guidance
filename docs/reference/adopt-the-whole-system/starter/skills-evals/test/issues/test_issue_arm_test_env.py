"""Regression coverage for arm environment cleanup under pytest and unittest.

pytest does not run unittest's module-cleanup queue, so every caller of
``install_arm_test_environment`` must expose cleanup through its module hook.
The setup decorator also rolls back immediately when setup raises or skips,
because pytest never calls the teardown hook after unsuccessful module setup.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEST_DIR = ROOT / "test"
CALLERS = (
    TEST_DIR / "run_tests.py",
    *(TEST_DIR / "issues" / name for name in (
        "test_issue_202_harness.py",
        "test_issue_97.py",
        "test_issue_arm_hardening.py",
        "test_issue_arm_network_sandbox.py",
        "test_issue_cli_json.py",
        "test_issue_guidance_violations.py",
        "test_issue_local_eval.py",
        "test_issue_model_tokens_effort.py",
        "test_issue_permission_mode.py",
        "test_issue_tool_trace.py",
    )),
)
ORIGINAL_PATH = "/opt/hostedtoolcache:/usr/local/sbin:/usr/bin:/bin"


def _exercise_caller(caller: Path) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory(prefix="arm-env-pytest-") as temp:
        probe = Path(temp)
        caller_source = json.dumps(str(caller))
        (probe / "test_first.py").write_text(
            "import importlib.util\n"
            "import os\n"
            "from pathlib import Path\n"
            f"spec = importlib.util.spec_from_file_location('_arm_env_caller', {caller_source})\n"
            "caller = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(caller)\n"
            "from arm_test_env import TEST_PATH\n"
            "setUpModule = caller.setUpModule\n"
            "tearDownModule = getattr(caller, 'tearDownModule', lambda: None)\n"
            "def test_caller_installed_arm_environment():\n"
            "    assert os.environ['PATH'] == TEST_PATH\n"
            "    mountinfo = Path(os.environ['SKILLS_EVALS_MOUNTINFO'])\n"
            "    assert mountinfo.read_bytes() == b''\n",
            encoding="utf-8",
        )
        (probe / "test_observer.py").write_text(
            "import os\n"
            "def test_original_path_is_restored():\n"
            "    assert os.environ['PATH'] == '/opt/hostedtoolcache:/usr/local/sbin:/usr/bin:/bin'\n"
            "def test_mountinfo_is_removed():\n"
            "    assert 'SKILLS_EVALS_MOUNTINFO' not in os.environ\n",
            encoding="utf-8",
        )
        # Keep the subprocess target a readable script for the suite recursion audit.
        (probe / "pytest_probe.py").write_text(
            "import sys\n"
            "import pytest\n"
            "raise SystemExit(pytest.main(sys.argv[1:]))\n",
            encoding="utf-8",
        )
        child_env = os.environ.copy()
        child_env["PATH"] = ORIGINAL_PATH
        child_env.pop("SKILLS_EVALS_MOUNTINFO", None)
        child_env.pop("PYTEST_ADDOPTS", None)
        child_env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        child_env["SKILLS_EVALS_USER_MEMORY"] = str(probe / "scratch-user-memory")
        child_env["PYTHONPATH"] = os.pathsep.join(
            [str(TEST_DIR), child_env.get("PYTHONPATH", "")]
        ).rstrip(os.pathsep)
        return subprocess.run(
            [sys.executable, "pytest_probe.py", "-q",
             "test_first.py", "test_observer.py"],
            cwd=probe,
            env=child_env,
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )


def _exercise_failed_setup(
    runner: str, failure: str, original_mountinfo: str | None,
) -> tuple[subprocess.CompletedProcess[str], dict, dict]:
    """Run a faulty real module followed by an independent environment observer."""
    with tempfile.TemporaryDirectory(prefix="arm-env-failed-setup-") as temp:
        probe = Path(temp)
        caller_source = json.dumps(str(TEST_DIR / "issues" / "test_issue_97.py"))
        (probe / "test_first.py").write_text(
            "import importlib.util\n"
            "import json\n"
            "import os\n"
            "import unittest\n"
            "from pathlib import Path\n"
            f"spec = importlib.util.spec_from_file_location('_arm_env_caller', {caller_source})\n"
            "caller = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(caller)\n"
            "from arm_test_env import TEST_PATH\n"
            "def fail_after_installation(path):\n"
            "    assert os.environ['PATH'] == TEST_PATH\n"
            "    assert Path(os.environ['SKILLS_EVALS_MOUNTINFO']).read_bytes() == b''\n"
            "    Path('installed.json').write_text(json.dumps({'installed': True}))\n"
            f"    raise {'RuntimeError' if failure == 'error' else 'unittest.SkipTest'}('injected setup {failure}')\n"
            "caller._memory_fingerprint = fail_after_installation\n"
            "setUpModule = caller.setUpModule\n"
            "tearDownModule = caller.tearDownModule\n"
            "class FirstTests(unittest.TestCase):\n"
            "    def test_body_must_not_run(self):\n"
            "        Path('first-body-ran').write_text('unexpected')\n"
            "        self.fail('test body ran after failed module setup')\n",
            encoding="utf-8",
        )
        (probe / "test_observer.py").write_text(
            "import json\n"
            "import os\n"
            "import unittest\n"
            "from pathlib import Path\n"
            "class ObserverTests(unittest.TestCase):\n"
            "    def test_original_environment_is_restored(self):\n"
            f"        self.assertEqual(os.environ.get('PATH'), {ORIGINAL_PATH!r})\n"
            f"        self.assertEqual(os.environ.get('SKILLS_EVALS_MOUNTINFO'), {original_mountinfo!r})\n"
            "        Path('observer.json').write_text(json.dumps({'restored': True}))\n",
            encoding="utf-8",
        )
        # A readable target lets the suite recursion audit inspect every child runner.
        if runner == "pytest":
            runner_source = (
                "import json\n"
                "from pathlib import Path\n"
                "import pytest\n"
                "class Reports:\n"
                "    def __init__(self):\n"
                "        self.collected = 0\n"
                "        self.reports = []\n"
                "    def pytest_collection_finish(self, session):\n"
                "        self.collected = len(session.items)\n"
                "    def pytest_runtest_logreport(self, report):\n"
                "        self.reports.append({'nodeid': report.nodeid, 'when': report.when,\n"
                "                             'outcome': report.outcome})\n"
                "reports = Reports()\n"
                "code = pytest.main(['-q', 'test_first.py', 'test_observer.py'], plugins=[reports])\n"
                "Path('result.json').write_text(json.dumps({'collected': reports.collected,\n"
                "                                         'reports': reports.reports}))\n"
                "raise SystemExit(code)\n"
            )
        else:
            runner_source = (
                "import json\n"
                "import unittest\n"
                "from pathlib import Path\n"
                "suite = unittest.defaultTestLoader.loadTestsFromNames(['test_first', 'test_observer'])\n"
                "collected = suite.countTestCases()\n"
                "result = unittest.TextTestRunner(verbosity=2).run(suite)\n"
                "Path('result.json').write_text(json.dumps({'collected': collected,\n"
                "    'run': result.testsRun, 'errors': len(result.errors),\n"
                "    'failures': len(result.failures), 'skipped': len(result.skipped)}))\n"
                "raise SystemExit(0 if result.wasSuccessful() else 1)\n"
            )
        (probe / "setup_probe.py").write_text(runner_source, encoding="utf-8")
        child_env = os.environ.copy()
        child_env["PATH"] = ORIGINAL_PATH
        if original_mountinfo is None:
            child_env.pop("SKILLS_EVALS_MOUNTINFO", None)
        else:
            child_env["SKILLS_EVALS_MOUNTINFO"] = original_mountinfo
        child_env.pop("PYTEST_ADDOPTS", None)
        child_env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        child_env["SKILLS_EVALS_USER_MEMORY"] = str(probe / "scratch-user-memory")
        child_env["PYTHONPATH"] = os.pathsep.join(
            [str(TEST_DIR), child_env.get("PYTHONPATH", "")]
        ).rstrip(os.pathsep)
        result = subprocess.run(
            [sys.executable, "setup_probe.py"], cwd=probe, env=child_env,
            text=True, capture_output=True, check=False, timeout=60,
        )
        markers = {
            "installed": (probe / "installed.json").is_file(),
            "restored": (probe / "observer.json").is_file(),
            "first_body_ran": (probe / "first-body-ran").exists(),
        }
        report = json.loads((probe / "result.json").read_text(encoding="utf-8"))
        return result, markers, report


class ArmTestEnvironmentCleanupTests(unittest.TestCase):
    pass


def _caller_test(caller: Path):
    def test(self: ArmTestEnvironmentCleanupTests) -> None:
        result = _exercise_caller(caller)
        self.assertEqual(
            result.returncode,
            0,
            f"pytest subprocess for {caller.name} failed:\n"
            f"{result.stdout}\n{result.stderr}",
        )

    return test


for _index, _caller in enumerate(CALLERS):
    setattr(
        ArmTestEnvironmentCleanupTests,
        f"test_{_index:02}_{_caller.stem}_restores_environment",
        _caller_test(_caller),
    )


def _failed_setup_test(runner: str, failure: str, original_mountinfo: str | None):
    def test(self: ArmTestEnvironmentCleanupTests) -> None:
        result, markers, report = _exercise_failed_setup(runner, failure, original_mountinfo)
        details = f"{result.stdout}\n{result.stderr}\n{report}"
        self.assertTrue(markers["installed"], details)
        self.assertFalse(markers["first_body_ran"], details)
        self.assertTrue(markers["restored"], details)
        self.assertEqual(report["collected"], 2, details)
        self.assertEqual(result.returncode, 1 if failure == "error" else 0, details)
        if runner == "pytest":
            meaningful = [
                item for item in report["reports"]
                if item["when"] == "call" or item["outcome"] != "passed"
            ]
            self.assertEqual(len(meaningful), 2, details)
            self.assertEqual(meaningful[0]["when"], "setup", details)
            self.assertEqual(meaningful[0]["outcome"], "failed" if failure == "error" else "skipped", details)
            self.assertTrue(meaningful[0]["nodeid"].startswith("test_first.py::"), details)
            self.assertEqual(meaningful[1]["when"], "call", details)
            self.assertEqual(meaningful[1]["outcome"], "passed", details)
            self.assertTrue(meaningful[1]["nodeid"].startswith("test_observer.py::"), details)
        else:
            self.assertEqual(report["run"], 1, details)
            self.assertEqual(report["errors"], int(failure == "error"), details)
            self.assertEqual(report["skipped"], int(failure == "skip"), details)
            self.assertEqual(report["failures"], 0, details)

    return test


for _runner in ("pytest", "unittest"):
    for _failure in ("error", "skip"):
        for _mountinfo in (None, "original-mountinfo-sentinel"):
            setattr(
                ArmTestEnvironmentCleanupTests,
                f"test_{_runner}_setup_{_failure}_restores_{'absent' if _mountinfo is None else 'existing'}_mountinfo",
                _failed_setup_test(_runner, _failure, _mountinfo),
            )
