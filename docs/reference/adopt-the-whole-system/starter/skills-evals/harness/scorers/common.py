"""Shared pieces for the objective scorers."""
from __future__ import annotations

import contextlib
import glob
import os
import shutil
import tempfile
from pathlib import Path


class ScorerError(Exception):
    """The check could not be evaluated. The runner scores it as a failure
    and flags it as an error; it is never skipped."""


def expand(workspace: Path, patterns: list[str]) -> list[Path]:
    """Files under `workspace` matching the globs. A pattern that is absolute
    or climbs out of the workspace is an error, and so is one matching nothing."""
    root = workspace.resolve()
    found: list[Path] = []
    for pattern in patterns:
        if os.path.isabs(pattern) or ".." in Path(pattern).parts:
            raise ScorerError("path pattern must stay inside the workspace")
        hits = sorted(glob.glob(str(root / pattern), recursive=True))
        files = [Path(h) for h in hits if os.path.isfile(h)]
        if not files:
            raise ScorerError(f"no file matches {pattern!r}")
        for f in files:
            if root not in f.resolve().parents:
                raise ScorerError("path resolves outside the workspace")
        found.extend(files)
    return found


@contextlib.contextmanager
def scratch_git_env(base: dict):
    """`base` plus a scratch HOME and an empty scratch global git config,
    created for this one call and removed after it. System config is off.

    Never point GIT_CONFIG_GLOBAL at /dev/null: a `git config --global` write
    run as root locks and renames over its target, so /dev/null becomes a
    regular file for every process on the host."""
    scratch = Path(tempfile.mkdtemp(prefix="eval-git-"))
    try:
        home = scratch / "home"
        home.mkdir()
        config = scratch / "gitconfig"
        config.write_bytes(b"")
        env = dict(base)
        env.pop("GIT_CONFIG_SYSTEM", None)
        env.update(HOME=str(home), GIT_CONFIG_GLOBAL=str(config), GIT_CONFIG_NOSYSTEM="1")
        yield env
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
