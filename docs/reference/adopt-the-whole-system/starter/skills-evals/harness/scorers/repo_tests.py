"""Hidden repository tests: the `repo_tests` objective check.

A real-work fixture is scored by the tests its pull request added. Those files
live OUTSIDE the seed, in an overlay directory under the fixture directory,
so the agent never sees them. At scoring time the final workspace is copied
to a scratch directory, the overlay is laid over the copy (replacing whatever
the agent left at those paths), and each selected test runs as its own
process with `command_succeeds`' isolation (ADR 0006): argv only, no shell, a
constant environment, a timeout no greater than 60 s, mandatory filesystem
and PID isolation, mandatory network isolation, and no program output in the
detail. The original fixture directory and final workspace stay hidden. The agent's own workspace is
never written.

Each `fail_to_pass` entry must pass, and so must each `pass_to_pass` entry.
Running one process per selected test is what keeps every process inside the
60 s cap (the owner, 2026-10-06: "Select tests (Recommended)") and decides each
test by its own exit code, never by parsing a runner's output.
"""

from __future__ import annotations

import math
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

from . import commands

DEFAULT_TIMEOUT_S = commands.DEFAULT_TIMEOUT_S
MAX_TIMEOUT_S = commands.MAX_TIMEOUT_S
MAX_TESTS = 16
MAX_TEST_ARG_CHARS = 512
MAX_OVERLAY_FILES = 128
MAX_OVERLAY_BYTES = 4 * 1024 * 1024
MAX_DETAIL_CHARS = 1000
SEED_DIR = "seed"

CONSTRAINT_KEYS = frozenset({"overlay", "argv", "fail_to_pass", "pass_to_pass",
                             "timeout_s"})
REQUIRED_KEYS = frozenset({"overlay", "argv", "fail_to_pass"})


class RepoTestsConfigError(ValueError):
    """A `repo_tests` check that cannot be run as written."""


def _argv(value) -> list[str]:
    if (not isinstance(value, list) or not value
            or any(not isinstance(arg, str) or "\x00" in arg for arg in value)
            or not value[0].strip()):
        raise RepoTestsConfigError("argv must be a nonempty list of strings")
    return list(value)


def _selection(value, key: str, required: bool) -> list[list[str]]:
    """Each entry is one test: a string, or a list of strings, appended to argv."""
    if value is None and not required:
        return []
    if not isinstance(value, list) or (required and not value):
        raise RepoTestsConfigError(f"{key} must be a nonempty list of tests"
                                   if required else f"{key} must be a list of tests")
    tests = []
    for item in value:
        parts = [item] if isinstance(item, str) else item
        if (not isinstance(parts, list) or not parts
                or any(not isinstance(part, str) or not part.strip()
                       or "\x00" in part or len(part) > MAX_TEST_ARG_CHARS
                       for part in parts)):
            raise RepoTestsConfigError(
                f"{key} entries must be a nonblank string or a nonempty list "
                f"of them, each at most {MAX_TEST_ARG_CHARS} characters")
        tests.append(list(parts))
    return tests


def _timeout(value) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not 0 < value <= MAX_TIMEOUT_S):
        raise RepoTestsConfigError(
            f"timeout_s must be a number of seconds above 0 and at most "
            f"{MAX_TIMEOUT_S}")
    return value


def overlay_files(fixture_dir: Path, overlay) -> list[tuple[Path, str]]:
    """Every overlay file as (source path, workspace-relative POSIX path).

    The overlay is a directory inside the fixture directory and outside its
    seed (a file under the seed is a file the agent can read). Only regular
    files and directories are accepted: a symlink could point the copy at
    anything on the machine. Bounded in file count and total bytes.
    """
    if not isinstance(overlay, str) or not overlay.strip() or "\x00" in overlay:
        raise RepoTestsConfigError("overlay must name a directory in the fixture")
    relative = Path(overlay)
    if relative.is_absolute() or ".." in relative.parts:
        raise RepoTestsConfigError(
            "overlay must be a relative path inside the fixture directory")
    try:
        root = Path(fixture_dir).resolve(strict=True)
        base = root / relative
        if base.is_symlink() or not base.is_dir():
            raise RepoTestsConfigError("overlay is not a directory")
        resolved = base.resolve(strict=True)
    except OSError as exc:
        raise RepoTestsConfigError("overlay is not a directory") from exc
    if resolved == root or not resolved.is_relative_to(root):
        raise RepoTestsConfigError(
            "overlay must be a directory inside the fixture directory")
    seed = root / SEED_DIR
    if resolved.is_relative_to(seed) or seed.is_relative_to(resolved):
        raise RepoTestsConfigError(
            "overlay must not be inside or contain the seed: the agent would "
            "see the hidden tests")
    files: list[tuple[Path, str]] = []
    total = 0
    for directory, dirnames, filenames in os.walk(resolved):
        for name in dirnames + filenames:
            path = Path(directory) / name
            mode = os.lstat(path).st_mode
            if stat.S_ISLNK(mode):
                raise RepoTestsConfigError("overlay must not contain symlinks")
            if stat.S_ISDIR(mode):
                continue
            if not stat.S_ISREG(mode):
                raise RepoTestsConfigError(
                    "overlay must hold only regular files and directories")
            rel = path.relative_to(resolved).as_posix()
            if rel.split("/", 1)[0] == ".git":
                raise RepoTestsConfigError("overlay must not write into .git")
            total += os.lstat(path).st_size
            files.append((path, rel))
            if len(files) > MAX_OVERLAY_FILES:
                raise RepoTestsConfigError(
                    f"overlay holds more than {MAX_OVERLAY_FILES} files")
            if total > MAX_OVERLAY_BYTES:
                raise RepoTestsConfigError(
                    f"overlay holds more than {MAX_OVERLAY_BYTES} bytes")
    if not files:
        raise RepoTestsConfigError("overlay holds no files")
    return sorted(files, key=lambda item: item[1])


def _config(fixture_dir: Path, overlay, argv, fail_to_pass, pass_to_pass,
            timeout_s) -> dict:
    config = {"argv": _argv(argv),
              "fail_to_pass": _selection(fail_to_pass, "fail_to_pass", True),
              "pass_to_pass": _selection(pass_to_pass, "pass_to_pass", False),
              "timeout_s": _timeout(timeout_s)}
    count = len(config["fail_to_pass"]) + len(config["pass_to_pass"])
    if count > MAX_TESTS:
        raise RepoTestsConfigError(
            f"{count} selected tests is more than {MAX_TESTS}: select the "
            "pull request's own tests")
    config["files"] = overlay_files(fixture_dir, overlay)
    return config


def validate_check(check: dict, fixture_dir: Path) -> None:
    """Fixture-load validation of one `repo_tests` check (keys, shape, size).

    The same rules `repo_tests` applies when it runs, raised before any arm
    starts rather than discovered after the agent has been paid for.
    """
    unknown = set(check) - {"id", "description", "type", "paths"} - CONSTRAINT_KEYS
    if unknown:
        raise RepoTestsConfigError(f"unknown key(s) {sorted(unknown)}")
    missing = REQUIRED_KEYS - set(check)
    if missing:
        raise RepoTestsConfigError(f"missing key(s) {sorted(missing)}")
    _config(fixture_dir, check["overlay"], check["argv"], check["fail_to_pass"],
            check.get("pass_to_pass"), check.get("timeout_s", DEFAULT_TIMEOUT_S))


def _lay_overlay(scratch: Path, files: list[tuple[Path, str]]) -> None:
    """Copy the overlay over the scratch copy, never through an agent symlink."""
    for source, rel in files:
        parts = rel.split("/")
        target = scratch
        for part in parts[:-1]:
            target = target / part
            if target.is_symlink() or (target.exists() and not target.is_dir()):
                target.unlink()
            if not target.exists():
                target.mkdir()
        target = target / parts[-1]
        if target.is_symlink() or target.is_file():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)
        shutil.copyfile(source, target)
        target.chmod(stat.S_IMODE(os.lstat(source).st_mode) & 0o755 | 0o600)


# The bundler variables that name a path inside the workspace (the others
# are flags or a path relative to the Gemfile).
BUNDLER_WORKSPACE_PATHS = ("BUNDLE_GEMFILE", "GEM_HOME")


def _run_one(argv: list[str], scratch: Path, root: Path, index: int,
             timeout_s, read_denied, python_deps: bool = False,
             bundler_env: dict | None = None) -> tuple[str, str]:
    """One selected test in a mandatory sandbox and fresh environment."""
    env_root = root / f"env-{index}"
    env_root.mkdir(mode=0o700)
    env = commands._environment(env_root, scratch, read_denied)
    if python_deps:
        private_bin, rest = env["PATH"].split(os.pathsep, 1)
        env["PATH"] = os.pathsep.join((private_bin, str(scratch / ".fixture-python" / "bin"), rest))
    if bundler_env:
        # The bundle was installed into the final workspace and copied with
        # it; the paths here are workspace-relative, re-rooted at the copy.
        env.update({name: str(scratch / value) if name in BUNDLER_WORKSPACE_PATHS else value
                    for name, value in bundler_env.items()})
    prefix, network = commands._sandbox_prefix(scratch, env_root, env, read_denied)
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        try:
            code = commands._run_sandboxed(prefix, argv, scratch, env, stdout,
                                           stderr, timeout_s)
        except OSError:
            return "spawn_failed", network
        except subprocess.TimeoutExpired:
            return "timeout", network
    return ("pass" if code == 0 else f"exit={code}"), network


def repo_tests(workspace: str, paths: list[str], overlay=None, argv=None,
               fail_to_pass=None, pass_to_pass=None,
               timeout_s=DEFAULT_TIMEOUT_S, seed=None, _python_deps=False,
               _bundler_env=None, *, read_denied=None) -> tuple[bool, str]:
    """Pass only when every selected hidden test exits 0 over the final workspace.

    `seed` is the fixture's seed directory, injected by `run_checks`; the
    overlay is resolved against its parent, the fixture directory. `paths`
    is registry metadata and is unused.
    """
    if seed is None:
        return False, "repo_tests_invalid: no fixture directory"
    try:
        config = _config(Path(seed).parent, overlay, argv, fail_to_pass,
                         pass_to_pass, timeout_s)
        final = Path(workspace).resolve(strict=True)
    except RepoTestsConfigError as exc:
        return False, f"repo_tests_invalid: {exc}"
    except OSError:
        return False, "repo_tests_invalid: workspace missing"
    with tempfile.TemporaryDirectory(prefix="objective-repo-tests-") as temporary:
        root = Path(temporary)
        scratch = root / "ws"
        try:
            shutil.copytree(final, scratch, symlinks=True)
            _lay_overlay(scratch, config["files"])
            scratch = scratch.resolve(strict=True)
            if _python_deps and config["argv"][0] == "python3":
                # Keep the lexical symlink path: resolving it loses venv discovery.
                venv = scratch / ".fixture-python"
                python = venv / "bin" / "python3"
                executable = (str(python) if not venv.is_symlink()
                              and not (venv / "bin").is_symlink()
                              and not (venv / "pyvenv.cfg").is_symlink()
                              and (venv / "pyvenv.cfg").is_file()
                              and python.resolve() == Path(commands._fixed_interpreter("python3") or "/").resolve()
                              and python.is_file() and os.access(python, os.X_OK) else None)
            else:
                executable = commands._executable(scratch, config["argv"][0])
        except (OSError, shutil.Error, RuntimeError, ValueError):
            return False, "repo_tests_copy_failed"
        if executable is None:
            return False, "repo_tests_invalid: argv[0] is not an allowed executable"
        command = [executable, *config["argv"][1:]]
        denied = [*(read_denied or ()), Path(seed).resolve().parent, final]
        bundler_env = None
        if _bundler_env:
            bundler_env = dict(_bundler_env)
            for name in BUNDLER_WORKSPACE_PATHS:
                if name not in bundler_env:
                    continue
                # Lexically: a test workspace may reach its bundle through a
                # symlink, as it reaches node_modules, and the copy keeps it.
                try:
                    bundler_env[name] = Path(bundler_env[name]).relative_to(final).as_posix()
                except ValueError:
                    return False, f"repo_tests_invalid: the bundler {name} is outside the workspace"
        network = "isolated"
        counts, failed, index = {}, [], 0
        for group in ("fail_to_pass", "pass_to_pass"):
            passed = 0
            for test in config[group]:
                try:
                    status, test_network = _run_one(command + test, scratch, root, index,
                                                    config["timeout_s"], denied, _python_deps,
                                                    bundler_env)
                except commands.ScorerSandboxUnavailable as exc:
                    return False, str(exc)
                if test_network == "unavailable":
                    network = "unavailable"
                index += 1
                if status == "pass":
                    passed += 1
                else:
                    failed.append(f"{' '.join(test)} ({status})")
            counts[group] = f"{passed}/{len(config[group])}"
    detail = (f"repo_tests fail_to_pass={counts['fail_to_pass']} "
              f"pass_to_pass={counts['pass_to_pass']} network={network}")
    if failed:
        detail += " failed: " + "; ".join(failed)
    if len(detail) > MAX_DETAIL_CHARS:
        detail = detail[:MAX_DETAIL_CHARS - 3] + "..."
    return not failed, detail
