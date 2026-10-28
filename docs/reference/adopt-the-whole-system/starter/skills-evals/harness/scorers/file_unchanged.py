"""file_unchanged: files matching `paths` are byte for byte what the seed
commit holds. A file the agent deleted or added under the pattern fails."""
from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path

from .common import ScorerError, scratch_git_env


def _git(workspace: Path, *args: str) -> bytes:
    try:
        with scratch_git_env({"PATH": "/usr/bin:/bin:/usr/local/bin"}) as env:
            done = subprocess.run(["git", "-C", str(workspace), *args], capture_output=True,
                                  timeout=60, check=True, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ScorerError(f"git failed: {type(exc).__name__}") from None
    return done.stdout


def score(check: dict, workspace: Path, **_) -> tuple[bool, str]:
    tracked = _git(workspace, "ls-tree", "-r", "-z", "--name-only", "HEAD").decode().split("\0")
    tracked = [t for t in tracked if t]
    changed = []
    matched = 0
    for pattern in check["paths"]:
        if pattern.startswith("/") or ".." in Path(pattern).parts:
            raise ScorerError("path pattern must stay inside the workspace")
        hits = [t for t in tracked if fnmatch.fnmatchcase(t, pattern)]
        if not hits:
            raise ScorerError(f"no seed file matches {pattern!r}")
        for rel in hits:
            matched += 1
            path = workspace / rel
            seed = _git(workspace, "show", f"HEAD:{rel}")
            if path.is_symlink() or not path.is_file() or path.read_bytes() != seed:
                changed.append(rel)
    return (not changed, f"{matched} files, {len(changed)} changed")
