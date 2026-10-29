"""A scratch checkout of this repository with example fixtures committed.

The adoption kit ships two sets of eval fixtures (its DECISIONS X9): the
workflow-path-audit skill's and the git-push guidance section's. Beyond
those, `evals/` holds only the config, the canaries and the propagation probe
until you add your own. Several tests exercise machinery that reads the
COMMITTED fixtures of a checkout (`git ls-files evals`, `evals/scheduled.yml`):
the dispatch validation, the weekly planner, the improve and guidance-improve
fire checks. They need more fixtures than the kit ships (a writing-adrs skill
with several, guidance sections with four fixtures), so they run against this
scratch checkout instead: every tracked file of the repository, plus the
invented, test-only fixtures under `test/fixtures/evals/` copied into its
`evals/`, and a schedule naming four of them (one is the kit's own
workflow-path-audit fixture), all in one commit (DECISIONS C8).

`checkout()` builds it once per process, in a fresh temporary directory
removed at exit; nothing is written under the repository itself.
"""

from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
#: The invented fixtures, laid out as they sit under a checkout's evals/.
FIXTURES = REPO_ROOT / "test" / "fixtures" / "evals"

#: The example schedule's fixtures, each with its readiness note.
SCHEDULED = (
    "evals/workflow-path-audit/required-check-routing",
    "evals/writing-adrs/bootstrap",
    "evals/guidance/anything-you-name-gets-its-link/handoff",
    "evals/guidance/anything-you-name-gets-its-link/waiting-on-review",
)

_IDENTITY = ["-c", "user.name=example-tests", "-c", "user.email=tests@example.com",
             "-c", "commit.gpgsign=false"]
_built: Path | None = None


def _git_env() -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["LC_ALL"] = "C"
    return env


def _tracked(root: Path) -> list[str]:
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=root, check=True,
                             capture_output=True, env=_git_env()).stdout
        names = [n for n in out.decode("utf-8", "surrogateescape").split("\0") if n]
    except (OSError, subprocess.CalledProcessError):
        names = []
    if names:
        return names
    # Not a git checkout (an unpacked archive): every file but caches.
    skip = {".git", "__pycache__", "node_modules", ".pytest_cache"}
    return [str(p.relative_to(root)) for p in root.rglob("*")
            if (p.is_file() or p.is_symlink()) and not skip & set(p.relative_to(root).parts)]


def _copy(src_root: Path, names, dest: Path) -> None:
    for name in names:
        src, dst = src_root / name, dest / name
        if not (src.is_symlink() or src.is_file()):
            continue  # deleted in the working tree
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_symlink():
            os.symlink(os.readlink(src), dst)
        else:
            shutil.copy2(src, dst)


def build(dest: Path) -> Path:
    """Write the scratch checkout into the empty directory `dest`."""
    _copy(REPO_ROOT, _tracked(REPO_ROOT), dest)
    shutil.copytree(FIXTURES, dest / "evals", dirs_exist_ok=True, symlinks=True)
    rows = "".join(f"  - path: {path}\n    readiness: \"Isolation pair, unpinned (the "
                   f"roster then named arm claude-sonnet-5-5 and judge "
                   f"claude-fable-5-1). Example fixture for the machinery "
                   f"tests; no run recorded.\"\n" for path in SCHEDULED)
    (dest / "evals" / "scheduled.yml").write_text(
        "# The scratch checkout's schedule (test/example_evals.py).\n"
        "fixtures:\n" + rows, encoding="utf-8")
    env = _git_env()
    for args in (["init", "-q", "-b", "main"], ["add", "-A"],
                 ["commit", "-q", "-m", "example checkout"]):
        subprocess.run(["git", *_IDENTITY, *args], cwd=dest, check=True,
                       capture_output=True, env=env)
    return dest


def checkout() -> Path:
    """The process-wide scratch checkout, built on first use."""
    global _built
    if _built is None:
        tmp = Path(tempfile.mkdtemp(prefix="example-evals-")).resolve()
        atexit.register(shutil.rmtree, tmp, ignore_errors=True)
        _built = build(tmp / "skills-evals")
    return _built
