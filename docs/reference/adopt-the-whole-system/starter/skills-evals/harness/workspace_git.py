"""Harness Git boundary for agent-controlled trees (ADR 0006 addendum).

Repository configuration is data to validate, never configuration to run.
Commands use a private metadata snapshot and constants-only process state.
"""
from __future__ import annotations

import atexit
import hashlib
import os
from pathlib import Path
import re
import secrets
import select
import shutil
import stat
import subprocess
import tempfile
import time

GIT = '/usr/bin/git'
DEFAULT_TIMEOUT_S = 10
# Staging copies the agent's tree into private /tmp, so a sparse file would
# otherwise be written out at full size (issue 350). Both caps are hang and
# disk guards, not quality bounds: the largest seed is ~4 MB in under 500
# files, and an unignored node_modules runs to tens of thousands of files and
# hundreds of MB. Apparent sizes are checked before copying; actual bytes are capped while streaming.
MAX_STAGED_BYTES = 2 * 1024 ** 3
MAX_STAGED_FILES = 200_000
# Git reads these only from a safely copied private work tree. Non-regular
# controls are refused during the copy, before Git could block on an open().
_SPECIAL_NAMES = frozenset({'.gitignore', '.gitattributes'})
# Commands that never read work-tree ignore or attribute files.
_NO_WORK_TREE_READS = frozenset({'init', 'rev-parse', 'remote'})
_OVERRIDES = (
    'core.fsmonitor=false', 'core.hooksPath=/dev/null', 'diff.external=',
    'core.pager=cat', 'core.sshCommand=', 'credential.helper=',
    'protocol.allow=never', 'core.attributesFile=/dev/null',
    'core.editor=true', 'sequence.editor=true', 'core.askPass=',
    'core.gitProxy=', 'core.alternateRefsCommand=', 'interactive.diffFilter=',
    'commit.gpgSign=false', 'tag.gpgSign=false', 'push.gpgSign=false',
    'log.showSignature=false', 'gpg.program=/usr/bin/false', 'maintenance.auto=false',
    'gc.auto=0', 'submodule.recurse=false', 'diff.ignoreSubmodules=all',
    'fetch.recurseSubmodules=false', 'core.untrackedCache=false',
    'user.email=ci@example.com', 'user.name=ci',
)
_CONTEXTS: dict[str, '_Context'] = {}


class _Deadline:
    def __init__(self, seconds: float):
        self.end = time.monotonic() + seconds

    def remaining(self) -> float:
        left = self.end - time.monotonic()
        if left <= 0:
            raise WorkspaceGitCollectionError('collection exceeded its time limit')
        return left


def _remaining(timeout: float | _Deadline) -> float:
    return timeout.remaining() if isinstance(timeout, _Deadline) else timeout


def _wait(proc: subprocess.Popen, timeout: float) -> int:
    import guidance
    guidance.check_timeout(timeout, 'workspace_git._wait(timeout=)',
                           guidance.SINK_TIMEOUT_REMEDY)
    return proc.wait(timeout=timeout)


class WorkspaceGitError(RuntimeError):
    """A named, recordable failure of harness Git on a workspace."""
    name = 'workspace_git_error'

    def __init__(self, reason: str):
        super().__init__(f'{self.name}: {reason}')


class WorkspaceGitTamperedError(WorkspaceGitError):
    """No Git command may run over this workspace's metadata."""
    name = 'workspace_git_tampered'


class WorkspaceGitCollectionError(WorkspaceGitError):
    """Collection could not finish (an unreadable file, a timeout)."""
    name = 'workspace_git_collection_failed'


def _refuse(reason: str) -> None:
    raise WorkspaceGitTamperedError(reason)


def _read_regular(path: Path) -> bytes:
    # O_NOFOLLOW closes the check/open symlink race. Private copies below
    # never hard-link agent files, so later modifications cannot affect Git.
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                _refuse('metadata is not a regular file')
            return stream.read()
    except OSError:
        _refuse('metadata cannot be read as a regular file')


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    if not path.exists() and not path.is_symlink():
        return digest.hexdigest()
    if path.is_symlink():
        _refuse('metadata symlink')
    if path.is_file():
        digest.update(_read_regular(path))
    elif path.is_dir():
        for entry in sorted(path.rglob('*')):
            name = str(entry.relative_to(path)).encode()
            digest.update(len(name).to_bytes(8, 'big'))
            digest.update(name)
            if entry.is_symlink():
                _refuse('metadata symlink')
            if entry.is_file():
                data = _read_regular(entry)
                digest.update(b'F' + len(data).to_bytes(8, 'big') + data)
            elif entry.is_dir():
                digest.update(b'D')
            else:
                _refuse('nonregular metadata')
    else:
        _refuse('nonregular metadata')
    return digest.hexdigest()


def _env(home: Path) -> dict[str, str]:
    return {'PATH': '/usr/bin:/bin', 'HOME': str(home), 'XDG_CONFIG_HOME': str(home),
            'LC_ALL': 'C', 'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_ATTR_NOSYSTEM': '1',
            'GIT_TERMINAL_PROMPT': '0', 'GIT_PAGER': 'cat',
            'GIT_OPTIONAL_LOCKS': '0', 'GIT_CEILING_DIRECTORIES': str(home)}


def _invoke(args: list[str], *, cwd: Path, home: Path, timeout: float,
            check: bool = False, input: str | None = None) -> subprocess.CompletedProcess:
    import guidance
    timeout = _remaining(timeout)
    guidance.check_timeout(timeout, 'workspace_git._invoke(timeout=)',
                           guidance.SINK_TIMEOUT_REMEDY)
    options = [item for option in _OVERRIDES for item in ('-c', option)]
    if not any(arg.startswith('--git-dir=') for arg in args):
        options.append('--git-dir=/dev/null')
    return subprocess.run([GIT, '--no-pager', *options, *args], cwd=cwd,
                          env=_env(home), check=check, capture_output=True,
                          text=True, errors='replace', input=input,
                          stdin=subprocess.DEVNULL if input is None else None,
                          timeout=timeout)


def _config(gitdir: Path, private: Path,
            timeout: float | _Deadline) -> list[tuple[str, str]]:
    raw = _read_regular(gitdir / 'config')
    copy = private / 'untrusted-config'
    copy.write_bytes(raw)
    # Git's own parser handles quoted subsections, multiline values, casing,
    # and duplicate keys. --no-includes prevents even reading include targets.
    result = _invoke(['config', '--no-includes', '--file', str(copy), '-z', '--list'],
                     cwd=private, home=private, timeout=_remaining(timeout))
    if result.returncode:
        _refuse('invalid repository configuration')
    entries = []
    for record in result.stdout.split('\0'):
        if not record:
            continue
        key, _, value = record.partition('\n')
        lower = key.lower()
        section = lower.split('.', 1)[0]
        executable = (section in {'include', 'includeif', 'filter', 'alias',
                                 'credential', 'gpg', 'pager', 'protocol', 'url',
                                 'hook', 'merge', 'trailer', 'difftool', 'mergetool'}
                      or lower in {'core.fsmonitor', 'core.hookspath', 'core.pager',
                                   'core.sshcommand', 'core.gitproxy', 'core.worktree',
                                   'core.attributesfile', 'core.alternateobjectsdirectory',
                                   'core.alternaterefscommand', 'interactive.difffilter', 'gc.recentobjectshook',
                                   'uploadpack.packobjectshook',
                                   'diff.external', 'extensions.worktreeconfig'}
                      or (section == 'diff' and lower.rsplit('.', 1)[-1]
                          in {'command', 'textconv'})
                      or (section == 'remote' and lower.rsplit('.', 1)[-1]
                          in {'vcs', 'proxy', 'uploadpack', 'receivepack'})
                      or lower in {'core.editor', 'sequence.editor', 'core.askpass',
                                   'core.pager', 'gpg.program', 'ssh.variant'})
        if executable:
            _refuse('executable repository configuration')
        entries.append((key, value))
    return entries


def _metadata(workspace: Path) -> Path:
    entry = workspace / '.git'
    if os.path.lexists(entry):
        return entry
    # Bare fixture repositories are data sources for read-only ref checks.
    # They never masquerade as a missing bookkeeping .git at a sealed root.
    if (workspace / 'HEAD').is_file() and (workspace / 'objects').is_dir():
        return workspace
    return entry


def _sandbox_placeholder(path: Path) -> bool:
    """Whether `path` is the empty file Claude Code's Linux sandbox leaves.

    Measured 2026-10-09 (CLI 2.1.292, .293 and .295): one sandboxed Bash call
    in a fresh repository leaves a 0-byte regular `.git/config.worktree`, the
    bubblewrap mount point for a protected path that did not exist (the
    distro `/usr/bin/bwrap` 0.9.0 and another build alike). Only that exact
    shape counts: a regular file this user owns, empty, with no other link.
    """
    try:
        info = os.lstat(path)
    except OSError:
        return False
    return (stat.S_ISREG(info.st_mode) and info.st_size == 0
            and info.st_nlink == 1 and info.st_uid == os.getuid())


def _refuse_redirects(gitdir: Path) -> None:
    # The sandbox leaves neither `commondir` nor `gitdir`, so both stay
    # refused in any shape, empty included. An empty `config.worktree` sets
    # nothing even if `extensions.worktreeConfig` were on, and
    # `_copy_metadata` never copies that file into the private metadata the
    # harness's own Git reads.
    for redirect in ('commondir', 'gitdir'):
        if os.path.lexists(gitdir / redirect):
            _refuse('redirected Git metadata')
    worktree_config = gitdir / 'config.worktree'
    if os.path.lexists(worktree_config) and not _sandbox_placeholder(worktree_config):
        _refuse('redirected Git metadata')


def _validate(workspace: Path, private: Path, timeout: float | _Deadline,
              baseline: tuple | None = None) -> list[tuple[str, str]]:
    gitdir = workspace / '.git' if baseline is not None else _metadata(workspace)
    try:
        identity = gitdir.lstat()
    except OSError:
        _refuse('.git is missing')
    if not stat.S_ISDIR(identity.st_mode):
        _refuse('.git is not a plain directory')
    if identity.st_uid != os.getuid():
        _refuse('.git ownership changed')
    _refuse_redirects(gitdir)
    for relative in ('objects/info/alternates', 'objects/info/http-alternates'):
        if os.path.lexists(gitdir / relative):
            _refuse('alternate object store')
    if (gitdir / 'info').is_symlink():
        _refuse('metadata info symlink')
    # Attributes are not refused: the private config defines no filter or
    # diff driver (executable driver keys are refused above) and the private
    # info/attributes unsets both, so `diff=python` or `filter=lfs` is inert.
    entries = _config(gitdir, private, timeout)
    if baseline is not None:
        current = (identity.st_dev, identity.st_ino, _digest(gitdir / 'config'),
                   _digest(gitdir / 'info'), _digest(gitdir / 'hooks'))
        if current != baseline:
            _refuse('baseline configuration, info, or hooks changed')
    return entries


def _copy_metadata(source: Path, destination: Path, *, top: bool = True,
                   deadline: _Deadline | None = None) -> None:
    # Only repository data is copied, with no hooks, configuration, includes,
    # alternates, or hard links. Reject symlinks before traversing directories.
    for entry in source.iterdir():
        if deadline is not None:
            deadline.remaining()
        if entry.name in {'config', 'config.worktree', 'hooks'} or (top and entry.name in {'info', 'commondir', 'gitdir'}):
            continue
        if entry.is_symlink():
            _refuse('metadata symlink')
        target = destination / entry.name
        if entry.is_dir():
            target.mkdir()
            _copy_metadata(entry, target, top=False, deadline=deadline)
        else:
            target.write_bytes(_read_regular(entry))
    # Root info/exclude is harmless fixture data and required by nested-repo
    # fixtures. Never copy the executable attributes file.
    exclude = source / 'info' / 'exclude'
    if top and (exclude.exists() or exclude.is_symlink()):
        (destination / 'info').mkdir(exist_ok=True)
        (destination / 'info' / 'exclude').write_bytes(_read_regular(exclude))


def _private_dir() -> Path:
    # Allocated without tempfile.mkdtemp: callers (and their tests) patch
    # that shared function to place the WORKSPACE, and private metadata
    # must never land on the same path.
    base = tempfile.gettempdir()
    for _ in range(100):
        path = Path(base) / ('trusted-git-' + secrets.token_hex(8))
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            continue
        return path
    raise FileExistsError('could not allocate a private Git directory')


class _Context:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.private = _private_dir()
        self.gitdir = self.private / 'metadata'
        self.baseline = None
        self.root_identity: tuple[int, int] | None = None
        self.saved_files: dict[str, bytes] = {}
        self.saved_dirs: list[str] = []

    def remember(self) -> None:
        self.saved_files = {}
        self.saved_dirs = []
        for entry in sorted(self.gitdir.rglob('*')):
            relative = str(entry.relative_to(self.gitdir))
            if entry.is_dir():
                self.saved_dirs.append(relative)
            else:
                self.saved_files[relative] = _read_regular(entry)

    def restore(self, source: '_Context') -> None:
        self.baseline = source.baseline
        self.gitdir.mkdir()
        for relative in source.saved_dirs:
            (self.gitdir / relative).mkdir(parents=True, exist_ok=True)
        for relative, data in source.saved_files.items():
            (self.gitdir / relative).write_bytes(data)

    def snapshot(self, entries: list[tuple[str, str]],
                 timeout: float | _Deadline) -> None:
        self.gitdir.mkdir()
        _copy_metadata(_metadata(self.workspace), self.gitdir,
                       deadline=timeout if isinstance(timeout, _Deadline) else None)
        bare = 'true' if _metadata(self.workspace) == self.workspace else 'false'
        (self.gitdir / 'config').write_text('[core]\n repositoryformatversion = 0\n bare = ' + bare + '\n')
        for key, value in entries:
            lower = key.lower()
            # Remote names/URLs are read-only scorer data; no rewrites or
            # transport options reach private Git. objectFormat is data too.
            if ((lower.startswith('remote.') and lower.rsplit('.', 1)[-1] == 'url')
                    or lower == 'extensions.objectformat'):
                _invoke(['config', '--file', str(self.gitdir / 'config'), key, value],
                        cwd=self.private, home=self.private,
                        timeout=_remaining(timeout), check=True)
        info = self.gitdir / 'info'
        info.mkdir(exist_ok=True)
        (info / 'attributes').write_text('* -filter !diff\n')

    def close(self):
        shutil.rmtree(self.private, ignore_errors=True)


def _logical_path(workspace: Path) -> Path:
    """Bind records before following a replaceable directory or parent."""
    return Path(os.path.abspath(os.fspath(workspace)))


def _record(workspace: Path) -> _Context | None:
    logical = _logical_path(workspace)
    # The original path wins even after it becomes a redirect. Canonical
    # aliases support harmless parent aliases such as macOS /tmp.
    return (_CONTEXTS.get(str(logical))
            or _CONTEXTS.get(str(logical.resolve())))


def _check_root(workspace: Path, context: _Context) -> None:
    try:
        identity = _logical_path(workspace).lstat()
    except OSError:
        _refuse('baseline workspace directory is missing')
    if (not stat.S_ISDIR(identity.st_mode)
            or (identity.st_dev, identity.st_ino) != context.root_identity):
        _refuse('baseline workspace directory changed')


def _remove_record(context: _Context) -> None:
    for key in [key for key, record in _CONTEXTS.items() if record is context]:
        del _CONTEXTS[key]
    context.close()


def seal(workspace: Path) -> None:
    """Record the harness-written baseline OUTSIDE the agent's workspace."""
    logical = _logical_path(workspace)
    identity = logical.lstat()
    if not stat.S_ISDIR(identity.st_mode):
        _refuse('workspace is not a plain directory')
    previous = _record(logical)
    if previous:
        _remove_record(previous)
    if not os.path.lexists(logical / '.git'):
        # Nothing harness-made to seal (only when a caller stubs `_git`).
        # Every later run() still validates whatever metadata it finds.
        return
    workspace = logical.resolve()
    context = _Context(workspace)
    try:
        entries = _validate(workspace, context.private, DEFAULT_TIMEOUT_S)
        context.snapshot(entries, DEFAULT_TIMEOUT_S)
        context.root_identity = (identity.st_dev, identity.st_ino)
        identity = (workspace / '.git').lstat()
        context.baseline = (identity.st_dev, identity.st_ino,
                            _digest(workspace / '.git' / 'config'),
                            _digest(workspace / '.git' / 'info'),
                            _digest(workspace / '.git' / 'hooks'))
        context.remember()
        context.close()
        _CONTEXTS[str(logical)] = context
        _CONTEXTS[str(workspace)] = context
    except subprocess.CalledProcessError as exc:
        context.close()
        raise WorkspaceGitCollectionError(
            f'git config exited {exc.returncode}') from None
    except BaseException:
        context.close()
        raise


def release(workspace: Path) -> None:
    context = _record(workspace)
    if context:
        _remove_record(context)


def _release_all() -> None:
    for context in set(_CONTEXTS.values()):
        context.close()
    _CONTEXTS.clear()


atexit.register(_release_all)


def _stage(context: _Context, command: list[str], timeout: _Deadline,
           check: bool) -> subprocess.CompletedProcess:
    """Stage only a private filesystem view, never discover nested config."""
    workspace = context.workspace
    nested: dict[Path, str | None] = {}
    for root, dirs, files in os.walk(workspace, followlinks=False):
        _remaining(timeout)
        dirs[:] = [name for name in dirs if name != '.git']
        current = Path(root)
        if current == workspace or not os.path.lexists(current / '.git'):
            continue
        dirs[:] = []
        entry = current / '.git'
        if entry.is_file() and not entry.is_symlink():
            # Linked fixture trees are visible in the structural report;
            # neither staging nor reporting follows their redirect file.
            nested[current] = None
            continue
        result = run('rev-parse', '--verify', 'HEAD', cwd=current,
                     timeout=_remaining(timeout))
        sha = result.stdout.strip()
        if result.returncode == 0 and re.fullmatch(r'[0-9a-fA-F]{40}|[0-9a-fA-F]{64}', sha):
            nested[current] = sha
        else:
            nested[current] = None
    stage = _view(context, timeout, skip=lambda path: path in nested)
    result = _invoke(['--git-dir=' + str(context.gitdir), '--work-tree=' + str(stage),
                      *command], cwd=context.private, home=context.private,
                     timeout=_remaining(timeout), check=check)
    if result.returncode == 0:
        for path, sha in nested.items():
            if sha is not None:
                _invoke(['--git-dir=' + str(context.gitdir), '--work-tree=' + str(stage),
                         'update-index', '--add', '--cacheinfo', '160000', sha,
                         str(path.relative_to(workspace))], cwd=context.private,
                        home=context.private, timeout=_remaining(timeout), check=True)
    return result


class _IgnoreQueries:
    """Two long-running Git readers of private controls, one without the index."""

    def __init__(self, context: _Context, stage: Path, deadline: _Deadline):
        self.context, self.stage, self.deadline = context, stage, deadline
        self.processes: dict[bool, subprocess.Popen] = {}

    def _process(self, no_index: bool) -> subprocess.Popen:
        if no_index not in self.processes:
            options = [item for option in _OVERRIDES for item in ('-c', option)]
            args = [GIT, '--no-pager', *options,
                    '--git-dir=' + str(self.context.gitdir),
                    '--work-tree=' + str(self.stage), 'check-ignore',
                    *(['--no-index'] if no_index else []),
                    '--verbose', '--non-matching', '--stdin', '-z']
            env = _env(self.context.private)
            env['GIT_FLUSH'] = '1'
            self.deadline.remaining()
            proc = subprocess.Popen(args, cwd=self.stage, env=env,
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL)
            os.set_blocking(proc.stdin.fileno(), False)
            os.set_blocking(proc.stdout.fileno(), False)
            self.processes[no_index] = proc
        return self.processes[no_index]

    def query(self, paths: list[str], gitlinks: set[str]) -> set[str]:
        groups: dict[bool, list[str]] = {False: [], True: []}
        for path in paths:
            parts = path.split('/')
            inside = any('/'.join(parts[:index]) in gitlinks
                         for index in range(1, len(parts)))
            groups[inside].append(path)
        ignored = set()
        for no_index, candidates in groups.items():
            if not candidates:
                continue
            proc = self._process(no_index)
            payload = b''.join(b'./' + os.fsencode(path) + b'\0'
                               for path in candidates)
            sent = 0
            output = bytearray()
            expected = 4 * len(candidates)
            fields_received = 0
            while sent < len(payload) or fields_received < expected:
                readable, writable, _ = select.select(
                    [proc.stdout], [proc.stdin] if sent < len(payload) else [],
                    [], self.deadline.remaining())
                if writable:
                    sent += os.write(proc.stdin.fileno(), payload[sent:sent + 65536])
                if readable:
                    chunk = os.read(proc.stdout.fileno(), 65536)
                    if not chunk:
                        raise WorkspaceGitCollectionError('git check-ignore ended early')
                    output.extend(chunk)
                    fields_received += chunk.count(0)
                if not readable and not writable:
                    self.deadline.remaining()
            fields = bytes(output).split(b'\0')
            if len(fields) != expected + 1 or fields[-1]:
                raise WorkspaceGitCollectionError('invalid git check-ignore response')
            for offset in range(0, expected, 4):
                pattern, path = fields[offset + 2], fields[offset + 3]
                if pattern and not pattern.startswith(b'!'):
                    ignored.add(os.fsdecode(path).removeprefix('./').rstrip('/'))
        return ignored

    def close(self) -> None:
        failure = None
        for proc in self.processes.values():
            try:
                try:
                    proc.stdin.close()
                except OSError:
                    # An exited check-ignore can have closed its input pipe.
                    pass
                try:
                    code = _wait(proc, self.deadline.remaining())
                except (subprocess.TimeoutExpired, WorkspaceGitCollectionError):
                    if isinstance(proc.pid, int) and proc.pid > 1:
                        try:
                            proc.kill()
                        except ProcessLookupError:
                            pass
                        _wait(proc, 1)
                    raise WorkspaceGitCollectionError(
                        'git check-ignore exceeded time limit') from None
                if code not in (0, 1):
                    raise WorkspaceGitCollectionError(
                        f'git check-ignore exited {code}')
            except (OSError, subprocess.TimeoutExpired,
                    WorkspaceGitCollectionError) as exc:
                if failure is None:
                    failure = (exc if isinstance(exc, WorkspaceGitCollectionError)
                               else WorkspaceGitCollectionError(
                                   'git check-ignore could not be reaped'))
            finally:
                proc.stdout.close()
        if failure is not None:
            raise failure

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()


def _valid_nested_marker(path: Path, context: _Context,
                         deadline: _Deadline) -> bool:
    # Git validates the .git directory or gitdir file before repository
    # setup. It does not load the nested configuration or run nested hooks.
    result = _invoke(['rev-parse', '--resolve-git-dir', str(path / '.git')],
                     cwd=context.private, home=context.private,
                     timeout=deadline.remaining())
    return result.returncode == 0


def _byte_limit() -> None:
    raise WorkspaceGitCollectionError(
        f'workspace has more than {MAX_STAGED_BYTES} bytes to stage')


def _copy_regular(source: Path, target: Path, mode: int, remaining: int,
                  deadline: _Deadline | None = None) -> int:
    # O_NONBLOCK plus fstat closes a regular-to-FIFO race without blocking.
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    copied = 0
    with os.fdopen(fd, 'rb') as stream:
        identity = os.fstat(stream.fileno())
        if not stat.S_ISREG(identity.st_mode):
            if source.name in _SPECIAL_NAMES:
                raise WorkspaceGitCollectionError(
                    f'{source.name} is not a regular file')
            return 0
        if identity.st_size > remaining:
            _byte_limit()
        with open(target, 'xb') as out:
            # At most remaining + 1 bytes are read, even if the source keeps
            # growing. The one-byte overflow probe also accepts exact bounds.
            while True:
                if deadline is not None:
                    deadline.remaining()
                chunk = stream.read(min(64 * 1024, remaining - copied + 1))
                if not chunk:
                    break
                if len(chunk) > remaining - copied:
                    _byte_limit()
                out.write(chunk)
                copied += len(chunk)
    os.chmod(target, stat.S_IMODE(mode) | stat.S_IRUSR | stat.S_IWUSR)
    return copied


def _controls(context: _Context, stage: Path, timeout: _Deadline, *, skip,
              gitlinks: set[str], indexed: set[str],
              prune_nested: bool) -> tuple[set[str], int, int, set[str]]:
    """Resolve directories parent-first using private ignore snapshots.

    Ignored subtrees contribute neither controls nor payloads to the budget.
    Indexed descendants keep their directories eligible, including attributes
    Git still reads for tracked files within an otherwise ignored directory.
    """
    workspace = context.workspace
    stage.mkdir()
    indexed_dirs = {parent.as_posix() for relative in indexed
                    for parent in Path(relative).parents if parent != Path('.')}
    ignored = set()
    ignored_dirs = set()
    pruned = set()
    staged_bytes = staged_files = 0

    def copy_control(path: Path) -> None:
        nonlocal staged_bytes, staged_files
        timeout.remaining()
        info = path.lstat()
        mode = info.st_mode
        if not (stat.S_ISLNK(mode) or stat.S_ISREG(mode)):
            raise WorkspaceGitCollectionError(
                f'{path.relative_to(workspace).as_posix()} is not a regular file')
        staged_files += 1
        if staged_files > MAX_STAGED_FILES:
            raise WorkspaceGitCollectionError(
                f'workspace has more than {MAX_STAGED_FILES} files to stage')
        target = stage / path.relative_to(workspace)
        if stat.S_ISLNK(mode):
            os.symlink(os.readlink(path), target)
        else:
            staged_bytes += _copy_regular(path, target, mode,
                                           MAX_STAGED_BYTES - staged_bytes, timeout)

    with _IgnoreQueries(context, stage, timeout) as queries:
        for root, dirs, files in os.walk(workspace, followlinks=False):
            timeout.remaining()
            current = Path(root)
            relative_root = current.relative_to(workspace).as_posix()
            target_root = stage / current.relative_to(workspace)
            ignore = current / '.gitignore'
            # Git never descends into excluded directories to load their ignore
            # rules. A tracked control remains eligible as indexed file content.
            if ('.gitignore' in dirs + files and not stat.S_ISDIR(ignore.lstat().st_mode)
                    and not skip(ignore)
                    and (relative_root not in ignored_dirs
                         or ignore.relative_to(workspace).as_posix() in indexed)):
                copy_control(ignore)
            paths = []
            keep = []
            for name in sorted(dirs) + sorted(files):
                timeout.remaining()
                path = current / name
                if name == '.git' or skip(path):
                    continue
                relative = path.relative_to(workspace).as_posix()
                paths.append(relative)
                if stat.S_ISDIR(path.lstat().st_mode):
                    target = target_root / name
                    target.mkdir()
                    marker_exists = os.path.lexists(path / '.git')
                    nested_repo = (marker_exists and prune_nested
                                   and _valid_nested_marker(path, context, timeout))
                    if prune_nested and (relative in gitlinks or nested_repo):
                        pruned.add(relative)
                        if relative not in gitlinks and nested_repo:
                            # Constants-only metadata marks an untracked nested
                            # repository for status, including -uall. No live
                            # nested metadata or descendants enter the read view.
                            marker = target / '.git'
                            marker.mkdir()
                            (marker / 'objects').mkdir()
                            (marker / 'refs').mkdir()
                            (marker / 'HEAD').write_text('ref: refs/heads/private\n')
                        continue
                    keep.append(name)
            excluded = queries.query(paths, gitlinks)
            for name in list(keep):
                relative = (current / name).relative_to(workspace).as_posix()
                if relative in excluded:
                    ignored_dirs.add(relative)
                    if relative in indexed_dirs:
                        excluded.remove(relative)
                    else:
                        keep.remove(name)
            ignored.update(excluded)
            # Attributes can affect tracked files even when the attributes file
            # itself is ignored. Copy after eligibility, before any attribute read.
            attributes = current / '.gitattributes'
            if ('.gitattributes' in dirs + files and not stat.S_ISDIR(attributes.lstat().st_mode)
                    and not skip(attributes)):
                copy_control(attributes)
            for relative in excluded:
                target = stage / relative
                if target.is_dir() and not target.is_symlink():
                    shutil.rmtree(target)
            dirs[:] = [name for name in dirs if name in keep]
    return ignored, staged_bytes, staged_files, pruned


def _view(context: _Context, timeout: _Deadline, *, skip,
          prune_nested: bool = False) -> Path:
    stage = context.private / 'stage'
    # --stage reads only private index metadata, without ignore/attribute
    # evaluation. Names become comparison strings, never filesystem targets.
    result = _invoke(['--git-dir=' + str(context.gitdir), 'ls-files', '--stage', '-z'],
                     cwd=context.private, home=context.private,
                     timeout=_remaining(timeout), check=True)
    indexed = {entry.partition('\t')[2] for entry in result.stdout.split('\0') if entry}
    gitlinks = {entry.partition('\t')[2] for entry in result.stdout.split('\0')
                if entry.startswith('160000 ')}
    ignored, staged_bytes, staged_files, pruned = _controls(
        context, stage, timeout, skip=skip, gitlinks=gitlinks, indexed=indexed,
        prune_nested=prune_nested)
    _copy_view(context.workspace, stage, skip=skip, ignored=ignored,
               prepared=(staged_bytes, staged_files), pruned=pruned,
               deadline=timeout if isinstance(timeout, _Deadline) else None)
    return stage


def _copy_view(workspace: Path, stage: Path, *, skip, ignored: set[str],
               prepared: tuple[int, int] | None = None,
               pruned: set[str] | None = None,
               deadline: _Deadline | None = None) -> None:
    """Copy eligible ordinary files into the stable private control view.

    FIFOs, sockets and devices are skipped as Git skips them. Apparent size
    rejects sparse oversized files before copying; streamed bytes enforce
    the cumulative bound even when files grow between lstat and EOF.
    """
    stage.mkdir(exist_ok=prepared is not None)
    staged_bytes, staged_files = prepared or (0, 0)
    for root, dirs, files in os.walk(workspace, followlinks=False):
        if deadline is not None:
            deadline.remaining()
        current = Path(root)
        target_root = stage / current.relative_to(workspace)
        keep = []
        for name in sorted(dirs) + sorted(files):
            if deadline is not None:
                deadline.remaining()
            path = current / name
            relative = path.relative_to(workspace).as_posix()
            if (name == '.git' or skip(path) or relative in ignored
                    or (pruned is not None and relative in pruned)):
                continue
            target = target_root / name
            if prepared is not None and name in _SPECIAL_NAMES:
                # Never reopen the live controls after Git evaluated ignores.
                # New controls appearing since the first scan are omitted.
                if target.is_dir() and not target.is_symlink():
                    keep.append(name)
                continue
            info = path.lstat()
            mode = info.st_mode
            if stat.S_ISLNK(mode) or stat.S_ISREG(mode):
                staged_files += 1
                if staged_files > MAX_STAGED_FILES:
                    raise WorkspaceGitCollectionError(
                        f'workspace has more than {MAX_STAGED_FILES} files to stage')
                if stat.S_ISREG(mode) and info.st_size > MAX_STAGED_BYTES - staged_bytes:
                    _byte_limit()
            if stat.S_ISLNK(mode):
                os.symlink(os.readlink(path), target)
            elif stat.S_ISDIR(mode):
                target.mkdir(exist_ok=True)
                keep.append(name)
            elif stat.S_ISREG(mode):
                staged_bytes += _copy_regular(path, target, mode,
                                               MAX_STAGED_BYTES - staged_bytes,
                                               deadline)
        dirs[:] = [name for name in dirs if name in keep]


def _export_data(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for entry in source.iterdir():
        target = destination / entry.name
        if entry.is_dir():
            _export_data(entry, target)
        else:
            data = _read_regular(entry)
            if target.exists() and _read_regular(target) == data:
                continue
            if os.path.lexists(target):
                target.unlink()
            target.write_bytes(data)


def validate(workspace: Path) -> None:
    """Check the sealed root before scoring, including runs without a judge."""
    stored = _record(workspace)
    if stored is not None:
        _check_root(workspace, stored)
    elif not os.path.lexists(Path(workspace) / '.git'):
        return  # never sealed and no metadata: no harness Git will run here
    private = _private_dir()
    try:
        _validate(Path(workspace).resolve(), private, DEFAULT_TIMEOUT_S,
                  stored.baseline if stored else None)
    finally:
        shutil.rmtree(private, ignore_errors=True)


def run(*args: str, cwd: Path, timeout: float = DEFAULT_TIMEOUT_S,
        check: bool = False) -> subprocess.CompletedProcess:
    """The only harness Git entrypoint, for initialization and collection.

    Sealed roots keep their private baseline refs and staging index. Other
    standalone repositories get a fresh validated snapshot for each read.
    Before sealing, init/add/commit export metadata for the arm to use.
    """
    import guidance
    guidance.check_timeout(timeout, 'workspace_git.run(timeout=)',
                           guidance.SINK_TIMEOUT_REMEDY)
    deadline = _Deadline(timeout)
    stored = _record(cwd)
    if stored is not None:
        _check_root(cwd, stored)
    workspace = Path(cwd).resolve()
    temporary = stored is None
    use_baseline = stored is not None and bool(args) and args[0] in {'add', 'diff'}
    context = _Context(workspace)
    if use_baseline:
        context.restore(stored)
    elif stored is not None:
        context.baseline = stored.baseline
    try:
        if args and args[0] == 'init':
            if os.path.lexists(workspace / '.git'):
                entry = workspace / '.git'
                if entry.is_symlink() or not entry.is_dir():
                    _refuse('.git is not a plain directory')
                _refuse_redirects(entry)
                if (entry / 'config').exists():
                    _validate(workspace, context.private, deadline)
                elif any(child.name != 'info' for child in entry.iterdir()):
                    _refuse('unexpected incomplete Git metadata')
                else:
                    _digest(entry / 'info')
            context.gitdir.mkdir()
            result = _invoke(['--git-dir=' + str(context.gitdir),
                              '--work-tree=' + str(workspace), 'init', '-q'], cwd=context.private,
                              home=context.private,
                              timeout=deadline.remaining(), check=check)
            # init records the explicit work-tree path; remove that bootstrap
            # option before exporting ordinary standalone metadata to the arm.
            _invoke(['config', '--file', str(context.gitdir / 'config'),
                     '--unset-all', 'core.worktree'], cwd=context.private,
                    home=context.private, timeout=deadline.remaining())
            # Like Git's own re-init, never overwrite existing metadata (a
            # fixture setup may have initialized the root with its own refs).
            def copy_missing(source: str, target: str) -> None:
                if not os.path.lexists(target):
                    shutil.copy2(source, target)
            shutil.copytree(context.gitdir, workspace / '.git', dirs_exist_ok=True,
                            copy_function=copy_missing)
            return result
        reads_work_tree = (not args or (args[0] not in _NO_WORK_TREE_READS
                           and args[:2] != ('worktree', 'list')))
        entries = _validate(workspace, context.private, deadline, context.baseline)
        if _metadata(workspace) == workspace and (not args or args[0] not in {'rev-parse', 'remote', 'log', 'show', 'worktree'}):
            _refuse('bare metadata supports read-only inspection')
        if not use_baseline:
            context.snapshot(entries, deadline)
        command = list(args)
        if command and command[0] in {'diff', 'log', 'show'}:
            command[1:1] = ['--no-ext-diff', '--no-textconv']
        if (command and command[0] == 'diff'
                and any(option in {'--cached', '--staged'} for option in
                        command[1:command.index('--') if '--' in command else len(command)])):
            # Cached gitlink additions are part of the judge's patch. The
            # global all override hides them; dirty still avoids inspecting
            # nested working trees while showing indexed commit changes.
            command.insert(1, '--ignore-submodules=dirty')
        if command and command[0] == 'add':
            result = _stage(context, command, deadline, check)
        else:
            view = (_view(context, deadline, skip=lambda path: False, prune_nested=True)
                    if reads_work_tree else workspace)
            result = _invoke(['--git-dir=' + str(context.gitdir),
                              '--work-tree=' + str(view), *command],
                             cwd=context.private, home=context.private,
                             timeout=deadline.remaining(), check=check)
        if temporary and command and command[0] in {'add', 'commit'}:
            # Setup happens before the arm. Keep its observable metadata in
            # sync; after seal all writes stay exclusively in private metadata.
            for name in ('objects', 'refs', 'logs'):
                source = context.gitdir / name
                if source.exists():
                    _export_data(source, workspace / '.git' / name)
            for name in ('HEAD', 'index', 'COMMIT_EDITMSG'):
                source = context.gitdir / name
                if source.exists():
                    (workspace / '.git' / name).write_bytes(source.read_bytes())
        # Scorers describe the logical workspace directory, not our private
        # copy. The operation was still executed against only private data.
        if command[:3] == ['rev-parse', '--path-format=absolute', '--git-dir'] and result.returncode == 0:
            result.stdout = str(_metadata(workspace)) + '\n'
        if command[:3] == ['worktree', 'list', '--porcelain'] and result.returncode == 0:
            result.stdout = result.stdout.replace('worktree ' + str(context.gitdir),
                                                  'worktree ' + str(workspace))
        if stored is not None and command and command[0] == 'add':
            context.remember()
            stored.saved_files = context.saved_files
            stored.saved_dirs = context.saved_dirs
        return result
    except subprocess.TimeoutExpired:
        raise WorkspaceGitCollectionError(
            f'git {args[0] if args else ""} exceeded {timeout}s') from None
    except subprocess.CalledProcessError as exc:
        # e.g. `working-tree-encoding=BOGUS-CHARSET` fails `git add` with 128.
        # Only the exit code is recorded; stderr echoes agent-chosen data.
        raise WorkspaceGitCollectionError(
            f'git {args[0] if args else ""} exited {exc.returncode}') from None
    except OSError as exc:
        raise WorkspaceGitCollectionError(
            f'{type(exc).__name__} ({exc.strerror or "error"}) while collecting') from None
    finally:
        context.close()
