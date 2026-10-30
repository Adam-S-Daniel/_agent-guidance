import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import supervisor  # noqa: E402


def test_start_returns_the_pid():
    with mock.patch("subprocess.Popen") as popen:
        popen.return_value.pid = 4321
        assert supervisor.start(["worker"]) == 4321
