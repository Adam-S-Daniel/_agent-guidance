"""Opt-in command checks inside a mandatory filesystem and PID sandbox.

See ADR 0006 for the mandatory filesystem, process and network boundaries.
No program output or exception text is included in published check details.
"""

from __future__ import annotations

import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_TIMEOUT_S = 30
MAX_TIMEOUT_S = 60
PROBE_TIMEOUT_S = 2
CLEANUP_TIMEOUT_S = 2
DIAGNOSTIC_BYTES = 4096
BWRAP_PATHS = ("/usr/bin/bwrap", "/bin/bwrap", str(Path.home() / ".local/bin/bwrap"))
SANDBOX_STARTUP_MARKER = b"scorer-sandbox-started\n"
SANDBOX_BOOTSTRAP = (
    "import os, sys\n"
    "fd = int(sys.argv[1])\n"
    "os.write(fd, b'scorer-sandbox-started\\n')\n"
    "os.close(fd)\n"
    "os.execv(sys.argv[2], sys.argv[2:])\n"
)
SYSTEM_PATH = ("/usr/bin", "/bin")
INTERPRETERS = {name: (f"/usr/bin/{name}", f"/bin/{name}")
                for name in ("bash", "sh", "python3", "node", "ruby")}


def _fixed_interpreter(name: str) -> str | None:
    """The candidate's resolved path, only when it stays inside the system mounts."""
    roots = [Path(root).resolve() for root in SYSTEM_MOUNTS if Path(root).exists()]
    for path in INTERPRETERS[name]:
        # A symlink such as /usr/bin/python3 -> /etc/alternatives/python3 dangles
        # in the sandbox, which binds neither /etc/alternatives nor its target.
        resolved = Path(os.path.realpath(path))
        if (resolved.is_file() and os.access(resolved, os.X_OK)
                and any(resolved.is_relative_to(root) for root in roots)):
            return str(resolved)
    return None


def _executable(workspace: Path, name: str) -> str | None:
    if Path(name).name.casefold() in ("claude", "claude.exe"):
        return None
    if name in INTERPRETERS:
        fixed = _fixed_interpreter(name)
        # Runners may install language runtimes outside fixed system locations.
        if fixed is not None or name not in ("node", "python3", "ruby"):
            return fixed
        return (_harness_node(workspace) if name == "node"
                else _harness_runtime(name, workspace))
    path = Path(name)
    path = path if path.is_absolute() else workspace / path
    path = path.resolve()
    if (not path.is_relative_to(workspace)
            or path.name.casefold() in ("claude", "claude.exe")):
        return None
    return str(path)


def _runtime_allowed(path: Path, workspace: Path | None, denied=()) -> bool:
    """Runtime discovery cannot reopen HOME, checkouts or evidence roots."""
    blocked = [Path.home().resolve(), Path(__file__).resolve().parents[2],
               *(Path(p).resolve() for p in denied or ())]
    if workspace is not None:
        blocked.append(workspace.resolve())
    return not any(path.is_relative_to(root) for root in blocked)


def _harness_runtime(name: str, workspace: Path | None, denied=()) -> str | None:
    found = shutil.which(name)
    if found is None or not os.path.isabs(found):
        return None
    try:
        resolved = Path(found).resolve()
        if not _runtime_allowed(resolved, workspace, denied):
            return None
        return str(resolved) if os.access(resolved, os.X_OK) else None
    except (OSError, RuntimeError, ValueError):
        return None


def _harness_node(workspace: Path | None, denied=()) -> str | None:
    """Expose only a trusted runner node when no system node exists."""
    if _fixed_interpreter("node") is not None:
        return None
    return _harness_runtime("node", workspace, denied)


def _environment(root: Path, workspace: Path | None = None, read_denied=()) -> dict[str, str]:
    """A new environment, never a filtered copy of a credential-bearing one."""
    directories = {"HOME": "home", "XDG_CONFIG_HOME": "config",
                   "XDG_CACHE_HOME": "cache", "XDG_DATA_HOME": "data",
                   "XDG_STATE_HOME": "state", "XDG_RUNTIME_DIR": "runtime",
                   "GH_CONFIG_DIR": "gh", "TMPDIR": "tmp",
                   "TMP": "tmp", "TEMP": "tmp"}
    env = {name: str(root / relative) for name, relative in directories.items()}
    for directory in set(env.values()):
        Path(directory).mkdir(mode=0o700)
    private_bin = root / "bin"
    private_bin.mkdir(mode=0o700)
    refusal = private_bin / "claude"
    refusal.write_text("#!/bin/sh\nexit 97\n", encoding="utf-8")
    refusal.chmod(0o700)
    # A script's bare `python3` finds the resolved interpreter here, not the
    # system symlink that dangles in the sandbox. It follows private_bin, so
    # anything a scorer inserts right after private_bin still wins.
    resolved_bin = root / "resolved-bin"
    resolved = {name: fixed for name in ("node", "python3", "ruby")
                if (fixed := _fixed_interpreter(name)) is not None
                and fixed not in INTERPRETERS[name]}
    if resolved:
        resolved_bin.mkdir(mode=0o700)
        for name, fixed in resolved.items():
            (resolved_bin / name).symlink_to(fixed)
    path = ":".join((str(private_bin), *([str(resolved_bin)] if resolved else []),
                     *SYSTEM_PATH))
    for name in ("node", "python3", "ruby"):
        if _fixed_interpreter(name) is not None:
            continue
        runtime = (_harness_node(workspace, read_denied) if name == "node"
                   else _harness_runtime(name, workspace, read_denied))
        if runtime is not None:
            # Only the selected executable is added to PATH, never its host bin.
            runtime_bin = root / f"{name}-bin"
            runtime_bin.mkdir(mode=0o700)
            (runtime_bin / name).symlink_to(runtime)
            path += f":{runtime_bin}"
    env.update(PATH=path, LANG="C", LC_ALL="C")
    return env


class ScorerSandboxUnavailable(RuntimeError):
    """A sanitized failure to establish the mandatory scoring boundary."""

    def __init__(self, exit_code=None):
        code = str(exit_code) if type(exit_code) is int else "-1"
        super().__init__(f"scorer_sandbox_unavailable exit={code}")


def _bubblewrap(workspace: Path | None = None, read_denied=()) -> str | None:
    """Resolve the harness's runner before hiding HOME, never agent code."""
    if not sys.platform.startswith("linux"):
        return None
    # PATH may name an agent-planted bwrap in the original workspace.
    # The HOME-local fallback is fixed when the harness module is imported.
    blocked = [Path(p).resolve() for p in read_denied
               if Path(p).resolve() != Path.home().resolve()]
    for candidate in BWRAP_PATHS:
        if not candidate or not os.path.isabs(candidate):
            continue
        try:
            path = Path(candidate).resolve(strict=True)
            if ((workspace is not None and path.is_relative_to(workspace.resolve()))
                    or any(path.is_relative_to(root) for root in blocked)):
                continue
            if path.is_file() and os.access(path, os.X_OK):
                return str(path)
        except (OSError, RuntimeError, ValueError):
            continue
    return None


def _read_denied(read_denied) -> list[Path]:
    """Trusted caller roots plus direct-call protection for harness and HOME."""
    roots = [Path(__file__).resolve().parents[2], Path.home(), *(read_denied or ())]
    out = []
    for root in roots:
        path = Path(root).resolve()
        if not path.exists():
            continue
        if path == Path("/"):
            raise ScorerSandboxUnavailable()
        if path not in out:
            out.append(path)
    return out


SYSTEM_MOUNTS = ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/lib32")
# The dynamic loader cache is the only host /etc state needed by these checks.
# No account database, resolver, machine identity, or configuration directory.
ETC_MOUNTS = ("/etc/ld.so.cache",)


def _ruby_prefix(binary: Path, workspace: Path, denied) -> Path | None:
    """The install prefix a Ruby outside /usr was built for, asked of the
    binary itself. A relocated copy (the cloud image's `/opt/ruby-<v>`, a
    second real directory beside `/opt/rbenv/versions/<v>`) keeps its
    compiled-in prefix in RUNPATH and `$LOAD_PATH`, so the sandbox must bind
    that prefix too or the loader finds no `libruby` and no stdlib."""
    import guidance
    guidance.check_timeout(PROBE_TIMEOUT_S, "_ruby_prefix timeout",
                           guidance.SINK_TIMEOUT_REMEDY)
    env = {"PATH": os.pathsep.join(SYSTEM_PATH), "LANG": "C", "LC_ALL": "C"}
    try:
        with subprocess.Popen([str(binary), "--disable-gems", "-rrbconfig", "-e",
                               "print RbConfig::CONFIG['prefix']"],
                              env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL) as probe:
            try:
                out, _ = probe.communicate(timeout=PROBE_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                probe.kill()
                return None
            if probe.returncode:
                return None
    except OSError:
        return None
    try:
        prefix = Path(out.decode("utf-8", "strict"))
        if not prefix.is_absolute() or not (prefix / "bin" / "ruby").is_file():
            return None
        if not _runtime_allowed(prefix.resolve(), workspace, denied):
            return None
    except (OSError, RuntimeError, ValueError, UnicodeDecodeError):
        return None
    return prefix


def _runtime_mounts(workspace: Path, denied) -> list[Path]:
    """Executable files and language libraries, never an entire tool cache."""
    candidates = [sys.executable]
    candidates.extend(found for name in ("python3", "node", "ruby")
                      if (found := _harness_runtime(name, workspace, denied)))
    paths = []
    for candidate in candidates:
        binary = Path(candidate).resolve()
        if not _runtime_allowed(binary, workspace, denied):
            continue
        # /usr and merged-/usr aliases are already in the system allowlist.
        if any(binary.is_relative_to(Path(root).resolve()) for root in SYSTEM_MOUNTS
               if Path(root).exists()):
            continue
        paths.append(binary)
        if binary.parent.name == "bin":
            lib = binary.parent.parent / "lib"
            if binary.name.startswith("python") and lib.is_dir():
                paths.extend(lib.glob("python[0-9]*"))
                paths.extend(lib.glob("libpython*.so*"))
            elif binary.name.startswith("ruby"):
                prefixes = [binary.parent.parent]
                compiled = _ruby_prefix(binary, workspace, denied)
                if compiled is not None and compiled not in prefixes:
                    prefixes.append(compiled)
                for prefix in prefixes:
                    paths.append(prefix / "bin" / "ruby")
                    paths.append(prefix / "lib" / "ruby")
                    paths.extend((prefix / "lib").glob("libruby*.so*"))
    # Keep each path by its own name, not its target: the loader asks for
    # `libruby.so.3.3`, a symlink to `libruby.so.3.3.6`, so a Ruby outside
    # /usr needs both bound, and `_sandbox_mounts` binds each to its target.
    return list(dict.fromkeys(path for path in paths if path.exists()
                             and _runtime_allowed(path.resolve(), workspace, denied)))


def _sandbox_mounts(workspace: Path, env_root: Path, denied) -> list[str]:
    """Start with an empty root, and bind only execution inputs and runtimes."""
    writable = (workspace.resolve(), env_root.resolve())
    denied = {Path(p).resolve() for p in denied}
    if (Path("/") in writable
            or any(path.is_relative_to(root) for path in denied for root in writable)):
        raise ScorerSandboxUnavailable()
    allowed = [Path(p) for p in (*SYSTEM_MOUNTS, *ETC_MOUNTS) if Path(p).exists()]
    allowed.extend(_runtime_mounts(workspace, denied))
    mounts = ["--tmpfs", "/tmp"]
    masks = {}
    for destination in allowed:
        source = destination.resolve()
        if any(source.is_relative_to(root) for root in denied):
            # A poisoned system runtime cannot be replaced by workspace code.
            raise ScorerSandboxUnavailable()
        mounts += ["--ro-bind", str(source), str(destination)]
        # A bind alias (e.g. /lib -> /usr/lib) needs the same descendant masks.
        for root in denied:
            if root.is_relative_to(source):
                masks[destination / root.relative_to(source)] = root.is_dir()
    covered = []
    for path, directory in sorted(masks.items(), key=lambda item: len(item[0].parts)):
        if any(path.is_relative_to(parent) for parent in covered):
            continue
        if directory:
            covered.append(path)
            mounts += ["--tmpfs", str(path), "--remount-ro", str(path)]
        else:
            mounts += ["--ro-bind", "/dev/null", str(path)]
    for path in writable:
        mounts += ["--bind", str(path), str(path)]
    return mounts


def _sandbox_prefix(workspace: Path, env_root: Path, env: dict[str, str],
                    read_denied=None) -> tuple[list[str], str]:
    """Probe the complete mandatory boundary once; any failure is closed."""
    import guidance
    guidance.check_timeout(PROBE_TIMEOUT_S, "_sandbox_prefix timeout",
                           guidance.SINK_TIMEOUT_REMEDY)
    denied = _read_denied(read_denied)
    executable = _bubblewrap(workspace, denied)
    if executable is None:
        raise ScorerSandboxUnavailable()
    mounts = _sandbox_mounts(workspace, env_root, denied)
    prefix = [executable, *mounts, "--unshare-pid", "--unshare-net",
              "--unshare-ipc", "--unshare-uts", "--proc", "/proc", "--dev", "/dev",
              "--die-with-parent", "--new-session", "--chdir", str(workspace), "--"]
    try:
        probe = subprocess.run(prefix + ["/usr/bin/true"], cwd=workspace, env=env,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=PROBE_TIMEOUT_S, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise ScorerSandboxUnavailable() from None
    if probe.returncode:
        raise ScorerSandboxUnavailable(probe.returncode)
    return prefix, "isolated"


def _sandbox_started(stream) -> bool:
    stream.seek(0)
    return stream.read(len(SANDBOX_STARTUP_MARKER) + 1) == SANDBOX_STARTUP_MARKER


def _run_sandboxed(prefix, argv, workspace, env, stdout, stderr, timeout_s):
    """Confirm actual sandbox startup, independently of the command's exit."""
    with tempfile.TemporaryFile() as started:
        fd = started.fileno()
        python = _fixed_interpreter("python3")
        if python is None:
            raise ScorerSandboxUnavailable()
        # bubblewrap's info-fd reports a fork before mount/loopback setup.
        # Isolated system Python reaches this marker only after all setup;
        # it closes the descriptor before replacing itself with agent code.
        launch = [*prefix, python, "-I", "-S", "-c", SANDBOX_BOOTSTRAP,
                  str(fd), *argv]
        try:
            code = _run_command(launch, workspace, env, stdout, stderr, timeout_s,
                                pass_fds=(fd,))
        except OSError:
            raise ScorerSandboxUnavailable() from None
        except subprocess.TimeoutExpired:
            if not _sandbox_started(started):
                raise ScorerSandboxUnavailable() from None
            raise
        if not _sandbox_started(started):
            raise ScorerSandboxUnavailable(code)
        return code


def _stop_process(proc) -> None:
    """Stop only our new session's group, including ordinary descendants."""
    import guidance
    guidance.check_timeout(CLEANUP_TIMEOUT_S, "_stop_process timeout",
                           guidance.SINK_TIMEOUT_REMEDY)
    pid = proc.pid
    # killpg(1, sig) is kill(-1, sig): it signals every process the user owns.
    if (type(pid) is not int or pid <= 1
            or pid in (os.getpid(), os.getpgrp())):
        raise ValueError("refusing to signal an unsafe process group id")
    try:
        if os.name == "posix":
            os.killpg(pid, signal.SIGKILL)
        elif proc.poll() is None:
            proc.kill()
    except ProcessLookupError:
        pass
    proc.wait(timeout=CLEANUP_TIMEOUT_S)


def _run_command(argv, workspace, env, stdout, stderr, timeout_s, *, pass_fds=()):
    """The guarded process sink, shared by every command-check entry path."""
    import guidance
    guidance.check_timeout(timeout_s, "_run_command timeout_s",
                           guidance.SINK_TIMEOUT_REMEDY)
    proc = subprocess.Popen(argv, cwd=workspace, env=env,
                            stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                            shell=False, start_new_session=os.name == "posix",
                            pass_fds=pass_fds)
    try:
        return proc.wait(timeout=timeout_s)
    finally:
        _stop_process(proc)


def _output_metadata(stdout, stderr) -> str:
    sizes = [os.fstat(stream.fileno()).st_size for stream in (stdout, stderr)]
    truncated = any(size > DIAGNOSTIC_BYTES for size in sizes)
    return (f"stdout_bytes={min(sizes[0], DIAGNOSTIC_BYTES)} "
            f"stderr_bytes={min(sizes[1], DIAGNOSTIC_BYTES)} "
            f"output={'truncated' if truncated else 'suppressed'}")


def command_succeeds(workspace: str, paths: list[str], argv=None,
                     timeout_s=DEFAULT_TIMEOUT_S, *, read_denied=None) -> tuple[bool, str]:
    """Pass only on exit zero from argv in the FINAL workspace.

    `paths` is registry metadata and is unused. No shell, interpolation, parent
    environment, user config, or credential is forwarded to the process.
    """
    if (not isinstance(argv, list) or not argv
            or any(not isinstance(arg, str) or "\x00" in arg for arg in argv)
            or not argv[0].strip()):
        return False, "command_invalid_argv"
    if (isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float))
            or not 0 < timeout_s <= MAX_TIMEOUT_S or not math.isfinite(timeout_s)):
        return False, "command_invalid_timeout"
    try:
        final_workspace = Path(workspace).resolve(strict=True)
        executable = _executable(final_workspace, argv[0])
    except (OSError, RuntimeError, ValueError):
        return False, "command_invalid_executable"
    if executable is None:
        return False, "command_invalid_executable"
    try:
        with tempfile.TemporaryDirectory(prefix="objective-command-") as temporary:
            env = _environment(Path(temporary), final_workspace, read_denied)
            prefix, network = _sandbox_prefix(final_workspace, Path(temporary), env, read_denied)
            if not os.path.isfile(executable) or not os.access(executable, os.X_OK):
                return False, f"command_spawn_failed network={network}"
            with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
                try:
                    returncode = _run_sandboxed(prefix, [executable, *argv[1:]],
                                                final_workspace, env, stdout,
                                                stderr, timeout_s)
                except OSError:
                    return False, f"command_spawn_failed network={network}"
                except subprocess.TimeoutExpired:
                    return False, (f"command_timeout network={network} "
                                   + _output_metadata(stdout, stderr))
                status = "command_success" if returncode == 0 else "command_nonzero"
                return returncode == 0, (f"{status} exit={returncode} network={network} "
                                        + _output_metadata(stdout, stderr))
    except ScorerSandboxUnavailable as exc:
        return False, str(exc)
    except OSError:
        return False, "command_spawn_failed"
