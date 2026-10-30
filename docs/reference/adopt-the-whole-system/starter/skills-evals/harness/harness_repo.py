"""Audited metadata reads of trusted harness and context checkouts.

ADR 0011's read fence denies the clone this harness runs from, the
directory holding it and its history, so it must find them: the checkout's
shared git directory and its main work tree. Every git call here reads that
trusted checkout or discovers context metadata to fence from an arm; none
ever runs on an agent workspace, which goes through
`workspace_git` instead. Hardened like `context.py`: a fixed git binary, no
hooks, no system or global configuration, no replace refs, and no ambient
`GIT_*` variable (a `GIT_DIR` would point the reads elsewhere).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

GIT = "/usr/bin/git"
HARNESS_ROOT = Path(__file__).resolve().parent.parent


class HarnessGitReadError(ValueError):
    """A command outside the reviewed metadata-read inventory."""


def _env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null",
               GIT_NO_REPLACE_OBJECTS="1")
    return env


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    """One read-only git command in `cwd`; OSError when git cannot start."""
    if args not in (
        ("rev-parse", "--path-format=absolute", "--git-common-dir"),
        ("config", "-z", "--local", "--includes", "--show-origin",
         "--get-regexp", r"^include(if\..*)?\.path$"),
        ("config", "--local", "--includes", "--get", "core.worktree"),
        ("rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir"),
        # run_eval.fixture_digest (#70): a fixture directory's tracked entries
        # and its untracked files, and the whole repository's entries for a
        # symlink's target. Never `ls-files -m`, which runs clean filters.
        ("ls-files", "-s", "-z", "--", "."),
        ("ls-files", "-o", "--exclude-standard", "-z", "--", "."),
        ("ls-files", "-s", "-z", "--", ":/"),
    ):
        raise HarnessGitReadError("harness metadata permits only audited Git reads")
    return subprocess.run([GIT, "-c", "core.hooksPath=/dev/null", "-C", str(cwd),
                           *args], env=_env(), stdin=subprocess.DEVNULL,
                          capture_output=True, encoding="utf-8", errors="surrogateescape")


def _git_out(*args: str, cwd: Path) -> str | None:
    try:
        result = _git(*args, cwd=cwd)
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def harness_git_common_dir(start: Path = HARNESS_ROOT) -> Path | None:
    """The clone's shared git directory (its whole history), or None."""
    out = _git_out("rev-parse", "--path-format=absolute", "--git-common-dir",
                   cwd=start)
    return Path(out).resolve() if out else None


def harness_clone_root(start: Path = HARNESS_ROOT) -> Path | None:
    """The main checkout of the clone a checkout belongs to, or None when it
    cannot be determined with certainty.

    In order: `core.worktree` in the repository's own config, includes
    followed, when set (it overrides every default, whatever the directory
    is called; an include git cannot read makes it None); the
    nearest ancestor of `start` whose `.git` (a directory, or a `gitdir:`
    file as `--separate-git-dir` and linked worktrees write) is that shared
    directory; the parent of a shared directory named `.git`, which is
    git's own default work tree. Git records no other path back from a
    `--separate-git-dir` directory to its checkout, so a linked worktree
    outside the main checkout, with the metadata elsewhere and no
    `core.worktree`, is None: the arm fails rather than guess. Not a git
    checkout (or no git): `start` itself.
    """
    start = Path(start)
    common = harness_git_common_dir(start)
    if common is None:
        return start.resolve()
    # The repository's own config, `[include]` and `[includeIf]` followed as
    # git follows them; an include git cannot read is a value we cannot see,
    # so the checkout is unknown rather than guessed.
    # `-z`: an `includeIf` condition may hold spaces (`gitdir:**/[ r]*`), so
    # each entry is `<origin>\0<key>\n<value>\0`, never split on a space.
    try:
        listed = _git("config", "-z", "--local", "--includes", "--show-origin",
                      "--get-regexp", r"^include(if\..*)?\.path$", cwd=start).stdout
    except OSError:
        return None
    fields = listed.split("\0")
    for origin, entry in zip(fields[0::2], fields[1::2]):
        _, _, value = entry.partition("\n")
        source = Path(origin.removeprefix("file:"))
        if not source.is_absolute():
            source = start / source
        target = Path(value).expanduser()
        if not target.is_absolute():
            target = source.parent / target
        if not value or not target.is_file() or not os.access(target, os.R_OK):
            return None
    configured = _git_out("config", "--local", "--includes", "--get",
                          "core.worktree", cwd=start)
    if configured:
        return (common / configured).resolve()
    for candidate in (start.resolve(), *start.resolve().parents):
        dot_git = candidate / ".git"
        if dot_git.is_dir() and dot_git.resolve() == common:
            return candidate
        if dot_git.is_file():
            text = dot_git.read_text(encoding="utf-8", errors="replace").strip()
            if text.startswith("gitdir:"):
                target = (candidate / text[len("gitdir:"):].strip()).resolve()
                if target == common:
                    return candidate
    if common.name == ".git":
        return common.parent
    return None
