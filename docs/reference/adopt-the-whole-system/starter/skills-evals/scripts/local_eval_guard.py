"""The credential rules scripts/local_eval.py applies, importable without the
harness, so the launch-time guard launcher (see `launcher_source`) can run
them in the instant before the real CLI starts.

Names and file/key names are reported; a value never is.
"""

from __future__ import annotations

import fnmatch
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.dont_write_bytecode = True

#: Refused when present in the environment at all (rule 1 of local_eval's
#: module docstring).
REFUSED_ENV_PREFIXES = ("ANTHROPIC_", "CLAUDE_CODE_USE_", "AWS_", "GOOGLE_",
                        "GCLOUD_", "CLOUDSDK_", "AZURE_")
REFUSED_ENV_NAMES = ("CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CONFIG_DIR")
#: CLAUDE_CODE_USE_* names that toggle a tool, not a provider.
NOT_PROVIDER_ENV = ("CLAUDE_CODE_USE_POWERSHELL_TOOL",)
REFUSED_ENV_SUBSTRINGS = ("API_KEY", "AUTH_TOKEN", "ACCESS_KEY", "SECRET",
                          "BEARER")

#: Settings keys that name a credential source. A settings `env` object is held
#: to the environment rule above.
REFUSED_SETTINGS_KEYS = ("apiKeyHelper", "awsAuthRefresh", "awsCredentialExport")

#: Proxy variables passed on; one whose URL embeds userinfo is refused.
PROXY_URL_NAMES = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")

#: The exit code a guard launcher uses when it refuses to start the CLI.
GUARD_EXIT = 87
#: The settings files the CLI itself loads from a project directory.
LOADED_SETTINGS_NAMES = ("settings.json", "settings.local.json")
#: Directories never worth walking.
PRUNED_DIRS = (".git", "node_modules")


class Refused(Exception):
    """A preflight refusal: printed, exit 2, no trial run."""


def refused_env_names(environ) -> list[str]:
    """Every name in `environ` the wrapper refuses to run under. Names only: a
    value is never read."""
    refused = []
    for name in environ:
        upper = str(name).upper()
        if upper in NOT_PROVIDER_ENV:
            continue
        if (upper in REFUSED_ENV_NAMES or upper.startswith(REFUSED_ENV_PREFIXES)
                or any(part in upper for part in REFUSED_ENV_SUBSTRINGS)):
            refused.append(str(name))
    return sorted(refused)


def proxy_userinfo_names(environ) -> list[str]:
    """Names of the proxy variables whose URL carries a username or password.
    Parsed, so an `@` in a query or fragment does not count. Names only."""
    named = []
    for name in PROXY_URL_NAMES:
        value = environ.get(name)
        if not value:
            continue
        try:
            parts = urlsplit(value if "://" in value else "//" + value)
            userinfo = parts.username is not None or parts.password is not None
        except ValueError:
            userinfo = True  # cannot show it carries none
        if userinfo:
            named.append(name)
    return sorted(named)


def check_settings_file(path: Path) -> None:
    """Raise Refused when the settings file at `path` names a credential
    source or cannot be shown not to. A missing file passes. Reports the file
    and the key, never a value."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return
    except (OSError, ValueError) as exc:
        raise Refused(f"cannot read settings file {path} "
                      f"({type(exc).__name__}); cannot show it names no "
                      "credential") from exc
    try:
        settings = json.loads(text)
    except ValueError as exc:
        raise Refused(f"settings file {path} is not valid JSON; cannot "
                      "show it names no credential") from exc
    if not isinstance(settings, dict):
        raise Refused(f"settings file {path} is not a JSON object; cannot "
                      "show it names no credential")
    keys = [k for k in REFUSED_SETTINGS_KEYS if k in settings]
    env_block = settings.get("env")
    if isinstance(env_block, dict):
        keys += [f"env.{name}" for name in refused_env_names(env_block)]
    if keys:
        raise Refused(f"settings file {path} sets {', '.join(keys)}: the "
                      "CLI loads it, and it names a credential source "
                      "or provider. Remove the key (or run elsewhere); "
                      "a local exhibit runs under the interactive login.")


def _walk_error(exc: OSError):
    raise Refused(f"cannot list {exc.filename} ({type(exc).__name__}); "
                  "cannot show it holds no settings file") from exc


def settings_in_tree(root: Path) -> list[Path]:
    """Every `.claude/settings*.json` at any depth under `root`, FOLLOWING
    symlinks (the workspace is built with `shutil.copytree`, which copies what
    a link points at) with each real directory visited once, so a link loop
    ends. A settings* directory is returned too, so the check refuses what it
    cannot read. A directory that cannot be listed is a refusal."""
    found, visited = [], {os.path.realpath(root)}
    for directory, dirs, files in os.walk(root, followlinks=True,
                                          onerror=_walk_error):
        keep = []
        for name in sorted(dirs):
            if name in PRUNED_DIRS:
                continue
            real = os.path.realpath(os.path.join(directory, name))
            if real not in visited:
                visited.add(real)
                keep.append(name)
        if os.path.basename(directory) == ".claude":
            found += [Path(directory, name) for name in (*dirs, *files)
                      if fnmatch.fnmatch(name, "settings*.json")]
        dirs[:] = keep  # in sorted order: the walk is deterministic
    return sorted(found)


def _claude_dir_settings(directory: Path, exact: bool = False) -> list[Path]:
    claude = Path(directory) / ".claude"
    try:
        names = os.listdir(claude)
    except (FileNotFoundError, NotADirectoryError):
        return []
    except OSError as exc:
        raise Refused(f"cannot list {claude} ({type(exc).__name__}); cannot "
                      "show it holds no settings file") from exc
    return [claude / name for name in sorted(names)
            if (name in LOADED_SETTINGS_NAMES if exact
                else fnmatch.fnmatch(name, "settings*.json"))]


def check_launch_cwd(cwd: str, skip_beneath: bool = False) -> None:
    """The settings pre-flight at the last moment, on the directory the CLI is
    about to start in: `.claude/settings*.json` in the cwd, the
    `settings.json` and `settings.local.json` in each parent up to the
    filesystem root (which assumes nothing about where the CLI stops looking
    for project settings), by the path as given and by its real path, and
    `.claude/settings*.json` beneath the cwd unless `skip_beneath` (the
    harness checkout's own tree, which the early pre-flight has checked as
    source)."""
    chain = []
    for start in (os.path.abspath(cwd), os.path.realpath(cwd)):
        current = Path(start)
        for directory in (current, *current.parents):
            if directory not in chain:
                chain.append(directory)
    starts = {Path(os.path.abspath(cwd)), Path(os.path.realpath(cwd))}
    for directory in chain:
        for path in _claude_dir_settings(directory, exact=directory not in starts):
            check_settings_file(path)
    if not skip_beneath:
        for path in settings_in_tree(Path(cwd)):
            check_settings_file(path)


def settings_files(home: Path, repo_root: Path, managed_files,
                   managed_dropins) -> list[Path]:
    """The settings files the CLI loads regardless of where it starts: the
    user's, the harness checkout's (the judge's cwd) and the managed policy's."""
    home, repo_root = Path(home), Path(repo_root)
    files = [home / ".claude" / "settings.json",
             home / ".claude" / "settings.local.json",
             repo_root / ".claude" / "settings.json",
             repo_root / ".claude" / "settings.local.json",
             *map(Path, managed_files)]
    for directory in map(Path, managed_dropins):
        if directory.is_dir():
            files += sorted(directory.glob("*.json"))
    return files


def check_all_settings(home, repo_root, managed_files, managed_dropins,
                       cwd=None, skip_beneath: bool = False) -> None:
    """The COMPLETE settings pre-flight, one code path for the early check
    (no `cwd`) and the launch-time guard (the directory the CLI starts in):
    user, harness-checkout and managed files, then, given a `cwd`, the walk in
    `check_launch_cwd`. Raises Refused naming the file and key."""
    for path in settings_files(home, repo_root, managed_files, managed_dropins):
        check_settings_file(path)
    if cwd is not None:
        check_launch_cwd(cwd, skip_beneath=skip_beneath)


def session_isolation_args(args) -> list:
    """`args` for a print-mode session (`-p`/`--print`), with
    `--strict-mcp-config` and, when argv names no `--setting-sources`,
    `--setting-sources project` prepended — each only when absent, so a
    caller's own choice (the judge's empty sources) stands. Anything else
    (`--version`, `plugin ...`) is returned unchanged. Never adds
    `--no-session-persistence`: a caller may `--resume` the session later.

    This is what reaches skill-creator's own `claude -p` calls (found on
    PATH), which no harness sink builds: without it they load the account's
    user settings, plugins and claude.ai MCP connectors."""
    args = list(args)
    if "-p" not in args and "--print" not in args:
        return args
    extra = []
    if "--strict-mcp-config" not in args:
        extra.append("--strict-mcp-config")
    if not any(a == "--setting-sources" or a.startswith("--setting-sources=")
               for a in args):
        extra += ["--setting-sources", "project"]
    return [*extra, *args]


def launcher_source(python: str, scripts_dir: str, real_cli: str,
                    repo_root: str, record: str, managed_files=(),
                    managed_dropins=()) -> str:
    """The text of the guard launcher a run points every child's CLAUDE_BIN
    (and the head of its PATH) at. It runs the whole pre-flight
    (`check_all_settings`: user, checkout and managed files, then its own cwd)
    and, on a refusal, writes the file and key to stderr and to `record`,
    exits GUARD_EXIT and does NOT start the CLI; otherwise it execs the real
    CLI with the environment unchanged and argv passed through
    `session_isolation_args`. A bare `--version` is not checked: it loads no
    settings and makes no model call."""
    return f'''#!{python}
import json, os, sys
sys.dont_write_bytecode = True
sys.path.insert(0, {scripts_dir!r})
import local_eval_guard as guard
REAL = {real_cli!r}
args = sys.argv[1:]
if args != ["--version"]:
    try:
        guard.check_all_settings(
            os.environ.get("HOME") or os.path.expanduser("~"), {repo_root!r},
            {list(map(str, managed_files))!r}, {list(map(str, managed_dropins))!r},
            cwd=os.getcwd(),
            skip_beneath=os.path.realpath(os.getcwd()) == {repo_root!r})
    except guard.Refused as exc:
        sys.stderr.write("local_eval guard: " + str(exc) + "\\n")
        try:
            with open({record!r}, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({{"cwd": os.getcwd(), "message": str(exc)}})
                         + "\\n")
        except OSError:
            pass
        sys.exit({GUARD_EXIT})
os.execv(REAL, [REAL, *guard.session_isolation_args(args)])
'''
