"""command_succeeds: `command` (split with shlex, no shell) exits 0 in the
workspace. The check files are already copied in. The environment is cleaned
to PATH, so the agent's variables and the runner's secrets are not visible;
HOME and the global git config are scratch, made for this one call.
A command that cannot start or times out is an error, not a plain failure."""
from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from .common import ScorerError, scratch_git_env

TIMEOUT_SECONDS = 60


def score(check: dict, workspace: Path, **_) -> tuple[bool, str]:
    argv = shlex.split(check["command"])
    if not argv:
        raise ScorerError("empty command")
    try:
        with scratch_git_env({"PATH": "/usr/local/bin:/usr/bin:/bin"}) as env:
            done = subprocess.run(argv, cwd=workspace, capture_output=True,
                                  timeout=TIMEOUT_SECONDS, stdin=subprocess.DEVNULL, env=env)
    except subprocess.TimeoutExpired:
        raise ScorerError("command timed out") from None
    except OSError as exc:
        raise ScorerError(f"command could not start: {type(exc).__name__}") from None
    # Output is never kept: it can hold the agent's text.
    return (done.returncode == 0, f"exit {done.returncode}")
