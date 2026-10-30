"""Host-independent PATH and mount metadata for arm-running tests."""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
import unittest
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from unittest import mock

TEST_PATH = os.pathsep.join([str(Path.home() / ".local" / "bin"),
                             "/usr/local/bin", "/usr/bin", "/bin"])
MOUNTINFO_ENV = "SKILLS_EVALS_MOUNTINFO"
_mountinfo: Path | None = None


def arm_test_environment() -> dict[str, str]:
    """Explicit Linux tools and an empty, process-owned mountinfo fixture.

    Used locally by install helpers too, so a deliberately poisoned parent
    environment cannot override a fixture installed at the module boundary.
    """
    global _mountinfo
    if _mountinfo is None:
        directory = Path(tempfile.mkdtemp(prefix="arm-test-env-"))
        atexit.register(shutil.rmtree, directory, ignore_errors=True)
        _mountinfo = directory / "mountinfo"
        _mountinfo.write_bytes(b"")
    return {"PATH": TEST_PATH, MOUNTINFO_ENV: str(_mountinfo)}


def install_arm_test_environment(setup: Callable[[], None]) -> Callable[[], None]:
    """Wrap module setup, restoring its environment if setup raises or skips.

    Tests measuring particular aliases or toolchains install their own
    mount metadata or PATH inside this scope. Callers must expose
    ``tearDownModule`` calling ``unittest.doModuleCleanups()``: pytest runs
    module teardown hooks but does not run unittest's module-cleanup queue.
    Neither runner calls module teardown after failed setup; roll back
    immediately in that case, including ``SkipTest`` and other BaseExceptions.
    """
    @wraps(setup)
    def wrapped_setup() -> None:
        patcher = mock.patch.dict(os.environ, arm_test_environment())
        patcher.start()
        unittest.addModuleCleanup(patcher.stop)
        try:
            setup()
        except BaseException:
            # stop() is idempotent when unittest later drains module cleanups.
            patcher.stop()
            raise

    return wrapped_setup
