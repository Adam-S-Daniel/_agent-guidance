"""Seed preparation for real-work fixtures: strip agent context, install deps.

A real-work seed is a fleet repository's own tree, and every fleet repository
carries the fleet's agent context: the managed half of `AGENTS.md`, the
`CLAUDE.md` bridge, `.claude/settings.json` with SessionStart hooks that
install skills (`skills-bootstrap.sh`) and write the guidance into user
memory (`fleet-memory.sh`, with its copy of the guidance in
`.claude/hooks/fleet-guidance.md`), and `skills.lock`. Arms run with
`--setting-sources project`, so a seed copied as-is would hand the
"without" arm the skills and the guidance the A/B is meant to withhold.

`strip_agent_context: true` removes all of it before the seed commit, and
keeps each repository's own `## Repo-specific additions` section (the owner,
2026-10-06: "Keep repo-specific (Recommended)"). `seed_guard` then checks the
workspace the agent will actually get and fails the arm if any stripped path
or fleet marker remains, so the strip is verified on every run rather than
trusted.

`deps:` installs a fixture's dependencies during setup, pinned, with network
(the owner, 2026-10-06: "Fetch in setup (Recommended)"). npm and bundler use the
repository's committed lockfile; Python uses complete exact pins from fixture
YAML and a fresh isolated venv. Installation failures are named `deps_failed`
errors.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import guidance
from scorers import commands, repo_tests

STRIP_KEY = "strip_agent_context"
DEPS_KEY = "deps"

# The line `_agent-guidance`'s sync.sh keeps everything at and below
# (`grep -qxF`, an exact whole line), so the same exact match is used here.
MARKER = "## Repo-specific additions"
GUIDANCE_FILES = ("AGENTS.md", "CLAUDE.md")
# Removed whole: `.claude/` holds the settings, the hooks, the fleet copy of
# the guidance and any committed skills; `skills.lock` names the bundles the
# bootstrap hook installs.
REMOVED_PATHS = (".claude", "skills.lock")
# Strings only the fleet's managed text carries. Any of them left in a
# workspace's AGENTS.md or CLAUDE.md after the strip fails the guard.
FLEET_MARKERS = (
    "BEGIN MANAGED SECTION",
    "END MANAGED SECTION",
    "Managed by [`_agent-guidance`]",
    "Managed by _agent-guidance",
)
BRIDGE_IMPORT = "@AGENTS.md"

MAX_DEPS = 4
DEPS_MANAGERS = ("npm", "pip", "bundler")
PYTHON_DIR = ".fixture-python"
MAX_REQUIREMENTS = 64
# Lexical requirement tokens only: pip options, URLs and markers are forbidden.
PINNED_REQUIREMENT = re.compile(
    r"([A-Za-z0-9][A-Za-z0-9._-]*)==([0-9]+(?:\.[0-9]+)*(?:(?:a|b|rc)[0-9]+|\.post[0-9]+|\.dev[0-9]+)?)\Z")
NPM_LOCKFILES = ("package-lock.json", "npm-shrinkwrap.json")
# A bundler entry installs the gems `Gemfile.lock` names into `vendor/bundle`
# beside that Gemfile (what `--deployment` installs to). The scorer runs the
# checker with `GEM_HOME` at that install (`bundler_environment`), so a plain
# `ruby` sees those gems first, beside Ruby's own bundled gems (minitest,
# rexml), the way the repository's CI runs the same spec against globally
# installed gems; `-rbundler/setup` would hide the bundled gems. The
# `BUNDLE_*` variables let `bundle exec` work too. `BUNDLE_GEMFILE` is one
# path, so a fixture may name only one bundler entry.
BUNDLER_GEMFILE = "Gemfile"
BUNDLER_LOCKFILE = "Gemfile.lock"
BUNDLE_PATH = "vendor/bundle"
DEPS_DETAIL_CHARS = 400


# ---------------------------------------------------------------------------
# strip_agent_context
# ---------------------------------------------------------------------------

def strip_requested(fixture: dict) -> bool:
    return fixture.get(STRIP_KEY) is True


def _stripped_text(text: str, name: str, agents_kept: bool) -> str | None:
    """The text to keep for one guidance file, or None to remove it."""
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.rstrip("\r\n") == MARKER:
            return "".join(lines[index:])
    body = [line.strip() for line in lines if line.strip()]
    if (name == "CLAUDE.md" and BRIDGE_IMPORT in body
            and all(line == BRIDGE_IMPORT
                    or (line.startswith("<!--") and line.endswith("-->"))
                    for line in body)):
        # The fleet's two-line bridge. A bare import keeps Claude Code reading
        # the AGENTS.md section that was kept, and only that.
        return BRIDGE_IMPORT + "\n" if agents_kept else None
    if any(marker in text for marker in FLEET_MARKERS):
        # Fleet-managed text in a shape with no marker line to cut at: drop
        # the file rather than guess where the repository's own text starts.
        return None
    return text


def _read_guidance_file(workspace: Path, path: Path) -> str | None:
    """A guidance file's text, following a symlink only inside the workspace."""
    try:
        target = path.resolve(strict=True)
        if not target.is_relative_to(workspace) or not target.is_file():
            return None
        return target.read_text(encoding="utf-8")
    except (OSError, RuntimeError, UnicodeDecodeError):
        return None


def strip_agent_context(workspace: Path) -> dict:
    """Remove the fleet's agent context from a workspace's root, in place.

    Returns `{"removed": [...], "rewritten": [...]}`, workspace-relative.
    """
    workspace = Path(workspace).resolve(strict=True)
    removed, rewritten = [], []
    for name in REMOVED_PATHS:
        path = workspace / name
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        else:
            continue
        removed.append(name)
    agents_kept = False
    for name in GUIDANCE_FILES:  # AGENTS.md first: CLAUDE.md may import it
        path = workspace / name
        if not os.path.lexists(path):
            continue
        text = _read_guidance_file(workspace, path)
        kept = None if text is None else _stripped_text(text, name, agents_kept)
        if kept is not None and kept == text and not path.is_symlink():
            agents_kept = agents_kept or name == "AGENTS.md"
            continue
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
        if kept is None:
            removed.append(name)
            continue
        path.write_text(kept, encoding="utf-8")
        rewritten.append(name)
        agents_kept = agents_kept or name == "AGENTS.md"
    return {"removed": removed, "rewritten": rewritten}


def agent_context_violations(workspace: Path) -> list[str]:
    """What fleet agent context the workspace root still carries, if any."""
    workspace = Path(workspace)
    found = [f"{name} is present" for name in REMOVED_PATHS
             if os.path.lexists(workspace / name)]
    for name in GUIDANCE_FILES:
        path = workspace / name
        if not os.path.lexists(path):
            continue
        if path.is_symlink() or not path.is_file():
            found.append(f"{name} is not a regular file")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            found.append(f"{name} is unreadable")
            continue
        found.extend(f"{name} carries the fleet marker {marker!r}"
                     for marker in FLEET_MARKERS if marker in text)
        lines = [line.rstrip("\r") for line in text.split("\n")]
        if MARKER in lines and lines.index(MARKER) != 0:
            found.append(f"{name} has text above {MARKER!r}")
    return found


def prepare_seed(workspace: Path, fixture: dict) -> None:
    """Strip the agent context from a freshly copied seed, when asked to."""
    if strip_requested(fixture):
        strip_agent_context(workspace)


@contextlib.contextmanager
def scoring_seed(seed, fixture: dict):
    """The seed directory a check should compare the workspace against.

    The workspace is stripped before the agent runs, so a check that reads
    the pristine seed (`files_unchanged`, `dir_listing_matches`,
    `non_remote_refs_unchanged`, ...) would report the strip itself as the
    agent's change. With `strip_agent_context:` set this yields a stripped
    copy of the seed, removed on exit; otherwise the seed itself.
    """
    if not strip_requested(fixture) or seed is None or not os.path.isdir(seed):
        yield seed
        return
    with tempfile.TemporaryDirectory(prefix="scoring-seed-") as temporary:
        copy = Path(temporary) / "seed"
        shutil.copytree(seed, copy, symlinks=True)
        strip_agent_context(copy)
        yield str(copy)


def seed_guard(workspace: Path, fixture: dict) -> dict | None:
    """None, or a `seed_not_stripped` error naming what remains.

    Run on the workspace exactly as the agent will get it (after `deps:` and
    `setup:`), so a setup step that writes agent context back is caught too.
    """
    if not strip_requested(fixture):
        return None
    found = agent_context_violations(workspace)
    if not found:
        return None
    return {"error": "seed_not_stripped",
            "detail": ("strip_agent_context is set and the seed still carries "
                       "agent context: " + "; ".join(found))}


# ---------------------------------------------------------------------------
# deps: install from the repository lockfile or fixture Python pins
# ---------------------------------------------------------------------------

def _deps_entries(value) -> list[dict]:
    """The validated `deps:` list (absent or null is an empty list)."""
    if value is None:
        return []
    if not isinstance(value, list) or not value or len(value) > MAX_DEPS:
        raise guidance.GuidanceError(
            f"`{DEPS_KEY}:` must be a list of 1 to {MAX_DEPS} entries")
    entries = []
    for index, entry in enumerate(value):
        where = f"`{DEPS_KEY}[{index}]`"
        if not isinstance(entry, dict):
            raise guidance.GuidanceError(f"{where} must be a mapping")
        manager = entry.get("manager")
        allowed = {"manager", "requirements"} if manager == "pip" else {"manager", "dir"}
        unknown = set(entry) - allowed
        if unknown:
            raise guidance.GuidanceError(f"{where} has unknown key(s) {sorted(unknown)}")
        manager = entry.get("manager")
        if manager not in DEPS_MANAGERS:
            raise guidance.GuidanceError(
                f"{where} `manager:` must be one of {list(DEPS_MANAGERS)}, got {manager!r}")
        if manager == "pip":
            requirements = entry.get("requirements")
            if (not isinstance(requirements, list) or not requirements
                    or len(requirements) > MAX_REQUIREMENTS):
                raise guidance.GuidanceError(
                    f"{where} `requirements:` must be a list of 1 to {MAX_REQUIREMENTS} exact pins")
            names = set()
            for requirement in requirements:
                match = (PINNED_REQUIREMENT.fullmatch(requirement)
                         if isinstance(requirement, str) and len(requirement) <= 200 else None)
                if match is None:
                    raise guidance.GuidanceError(f"{where} requires plain package==version pins")
                name = re.sub(r"[-_.]+", "-", match[1]).lower()
                if name in names:
                    raise guidance.GuidanceError(f"{where} repeats a package")
                names.add(name)
            if any(item["manager"] == "pip" for item in entries):
                raise guidance.GuidanceError("`deps:` may contain only one pip entry")
            entries.append({"manager": "pip", "requirements": list(requirements)})
            continue
        directory = entry.get("dir", ".")
        if (not isinstance(directory, str) or not directory.strip()
                or "\x00" in directory or Path(directory).is_absolute()
                or ".." in Path(directory).parts):
            raise guidance.GuidanceError(
                f"{where} `dir:` must be a relative path inside the workspace")
        if manager == "bundler" and any(item["manager"] == "bundler" for item in entries):
            raise guidance.GuidanceError("`deps:` may contain only one bundler entry")
        entries.append({"manager": manager, "dir": directory})
    return entries


def bundler_environment(workspace: Path, fixture: dict) -> dict[str, str]:
    """The variables that make Ruby load a bundler entry's installed gems:
    `GEM_HOME` at the one `vendor/bundle/ruby/<api version>` directory the
    install made (absent until it has run), and for `bundle exec` the
    Gemfile, the `vendor/bundle` path beside it (relative, as bundler
    resolves `BUNDLE_PATH` against the Gemfile's directory), frozen to the
    lockfile and shut off from the host's gems. Empty without an entry."""
    try:
        entries = _deps_entries(fixture.get(DEPS_KEY))
    except guidance.GuidanceError:
        return {}
    for entry in entries:
        if entry["manager"] != "bundler":
            continue
        directory = Path(workspace).resolve() / entry["dir"]
        env = {"BUNDLE_GEMFILE": str(directory / BUNDLER_GEMFILE),
               "BUNDLE_PATH": BUNDLE_PATH, "BUNDLE_FROZEN": "true",
               "BUNDLE_DISABLE_SHARED_GEMS": "true"}
        installed = [p for p in (directory / BUNDLE_PATH / "ruby").glob("*")
                     if p.is_dir() and not p.is_symlink()]
        if len(installed) == 1:
            env["GEM_HOME"] = str(installed[0])
        return env
    return {}


def _install_bundler(workspace: Path, directory: Path, index: int, entry: dict,
                     env: dict, timeout) -> dict | None:
    """`bundle install` from the directory's committed Gemfile.lock into
    `vendor/bundle` beside it, frozen (the lockfile is never rewritten) and
    without the host's gems. Bundler's own config and cache go to a
    temporary home, so the workspace gains only the installed gems."""
    for name in (BUNDLER_GEMFILE, BUNDLER_LOCKFILE):
        if not (directory / name).is_file() or (directory / name).is_symlink():
            return _deps_error(index, entry, f"no {name}: dependencies install only "
                               "from the repository's committed lockfile, pinned")
    if os.path.lexists(directory / BUNDLE_PATH):
        return _deps_error(index, entry, f"reserved {BUNDLE_PATH} path already exists")
    bundle = shutil.which("bundle", path=env.get("PATH", ""))
    if bundle is None:
        return _deps_error(index, entry, "bundle is not on the arm's PATH")
    argv = [bundle, "install", "--quiet"]
    with tempfile.TemporaryDirectory(prefix="deps-bundler-") as temporary, \
            tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        root = Path(temporary)
        child_env = dict(env, **bundler_environment(workspace, {DEPS_KEY: [entry]}),
                         BUNDLE_APP_CONFIG=str(root / "app-config"),
                         BUNDLE_USER_HOME=str(root / "user-home"),
                         BUNDLE_SILENCE_ROOT_WARNING="true", BUNDLE_JOBS="4")
        try:
            code = commands._run_command(argv, directory, child_env, stdout, stderr, timeout)
        except OSError:
            return _deps_error(index, entry, "bundle could not be started")
        except subprocess.TimeoutExpired:
            return _deps_error(index, entry, f"bundle install timed out after {timeout}s")
        if code != 0:
            stderr.seek(0)
            tail = stderr.read()[-DEPS_DETAIL_CHARS:].decode("utf-8", "replace")
            return _deps_error(index, entry,
                               f"bundle install exited {code} using {BUNDLER_LOCKFILE}: {tail.strip()}")
    return None


def _deps_error(index: int, entry: dict, message: str) -> dict:
    return {"error": "deps_failed",
            "detail": f"deps[{index}] ({entry['manager']} in {entry.get('dir', PYTHON_DIR)!r}): {message}"}


def _install_python(workspace: Path, index: int, entry: dict, timeout) -> dict | None:
    """Create a fresh venv and install only the fixture's complete exact pins."""
    venv = workspace / PYTHON_DIR
    if os.path.lexists(venv):
        return _deps_error(index, entry, "reserved Python environment path already exists")
    python = commands._fixed_interpreter("python3")
    if python is None:
        return _deps_error(index, entry, "system python3 is unavailable")
    try:
        venv.mkdir(mode=0o700)
    except OSError:
        return _deps_error(index, entry, "Python environment path could not be created")
    with tempfile.TemporaryDirectory(prefix="deps-python-") as temporary:
        root = Path(temporary)
        env = commands._environment(root, workspace)
        requirements = root / "requirements.txt"
        requirements.write_text("\n".join(entry["requirements"]) + "\n", encoding="utf-8")
        steps = [
            ("venv", [python, "-I", "-m", "venv", str(venv)]),
            ("pip install", [str(venv / "bin" / "python3"), "-I", "-m", "pip",
                             "--isolated", "install", "--no-deps", "--only-binary=:all:",
                             "--disable-pip-version-check", "--no-input",
                             "--requirement", str(requirements)]),
        ]
        for label, argv in steps:
            with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
                try:
                    code = commands._run_command(argv, workspace, env, stdout, stderr, timeout)
                except OSError:
                    return _deps_error(index, entry, f"{label} could not be started")
                except subprocess.TimeoutExpired:
                    return _deps_error(index, entry, f"{label} timed out after {timeout}s")
                if code != 0:
                    return _deps_error(index, entry, f"{label} exited {code}; output suppressed")
    return None


def install_deps(workspace: Path, fixture: dict, env: dict, timeout) -> dict | None:
    """Install every `deps:` entry; None, or a `deps_failed` error dict.

    npm runs `npm ci`, which installs exactly the versions and integrity
    hashes the committed lockfile names and refuses a lockfile out of step
    with package.json; `--ignore-scripts` keeps dependency lifecycle scripts
    from running. bundler runs a frozen `bundle install` from Gemfile.lock
    (`_install_bundler`). Network is allowed here and only here.
    """
    try:
        entries = _deps_entries(fixture.get(DEPS_KEY))
    except guidance.GuidanceError as exc:
        return {"error": "deps_failed", "detail": str(exc)}
    workspace = Path(workspace).resolve(strict=True)
    for index, entry in enumerate(entries):
        if entry["manager"] == "pip":
            error = _install_python(workspace, index, entry, timeout)
            if error is not None:
                return error
            continue
        directory = (workspace / entry["dir"]).resolve()
        if not directory.is_relative_to(workspace) or not directory.is_dir():
            return _deps_error(index, entry, "the directory does not exist in the workspace")
        if entry["manager"] == "bundler":
            error = _install_bundler(workspace, directory, index, entry, env, timeout)
            if error is not None:
                return error
            continue
        lockfile = next((name for name in NPM_LOCKFILES
                         if (directory / name).is_file()
                         and not (directory / name).is_symlink()), None)
        if lockfile is None:
            return _deps_error(
                index, entry,
                f"no lockfile ({' or '.join(NPM_LOCKFILES)}): dependencies "
                "install only from the repository's committed lockfile, pinned")
        npm = shutil.which("npm", path=env.get("PATH", ""))
        if npm is None:
            return _deps_error(index, entry, "npm is not on the arm's PATH")
        argv = [npm, "ci", "--ignore-scripts", "--no-audit", "--no-fund"]
        with tempfile.TemporaryDirectory(prefix="deps-cache-") as cache, \
                tempfile.TemporaryFile() as stdout, \
                tempfile.TemporaryFile() as stderr:
            child_env = dict(env, npm_config_cache=cache,
                             npm_config_update_notifier="false")
            try:
                code = commands._run_command(argv, directory, child_env, stdout,
                                             stderr, timeout)
            except OSError:
                return _deps_error(index, entry, "npm could not be started")
            except subprocess.TimeoutExpired:
                return _deps_error(index, entry, f"npm ci timed out after {timeout}s")
            if code != 0:
                stderr.seek(0)
                tail = stderr.read()[-DEPS_DETAIL_CHARS:].decode("utf-8", "replace")
                return _deps_error(index, entry,
                                   f"npm ci exited {code} using {lockfile}: {tail.strip()}")
    return None


# ---------------------------------------------------------------------------
# fixture-load validation
# ---------------------------------------------------------------------------

def _check_lists(fixture: dict):
    yield "objective_checks", fixture.get("objective_checks")
    arms = fixture.get("arms")
    if isinstance(arms, dict):
        for name, arm in arms.items():
            if isinstance(arm, dict):
                yield f"arms.{name}.objective_checks", arm.get("objective_checks")


def validate_fixture(fixture: dict, eval_dir: Path) -> None:
    """`strip_agent_context:`, `deps:` and every `repo_tests` check, at load.

    Raises GuidanceError, which `main()` reports as a fixture configuration
    error (exit 2) before any arm starts.
    """
    where = Path(eval_dir) / "fixture.yaml"
    strip = fixture.get(STRIP_KEY)
    if strip is not None and not isinstance(strip, bool):
        raise guidance.GuidanceError(
            f"{where}: `{STRIP_KEY}:` must be true or false, got {strip!r}")
    if fixture.get(DEPS_KEY) is not None and fixture.get("subject") == "guidance":
        raise guidance.GuidanceError(
            f"{where}: `{DEPS_KEY}:` runs in the skill subject's setup; a "
            "guidance fixture never runs setup, so it would be ignored")
    try:
        _deps_entries(fixture.get(DEPS_KEY))
    except guidance.GuidanceError as exc:
        raise guidance.GuidanceError(f"{where}: {exc}") from None
    if os.path.lexists(Path(eval_dir) / "seed" / PYTHON_DIR):
        raise guidance.GuidanceError(f"{where}: seed carries reserved {PYTHON_DIR} path")
    for key, checks in _check_lists(fixture):
        if not isinstance(checks, list):
            continue
        for check in checks:
            if not isinstance(check, dict) or check.get("type") != "repo_tests":
                continue
            try:
                repo_tests.validate_check(check, Path(eval_dir))
                files = repo_tests.overlay_files(Path(eval_dir), check["overlay"])
                if (os.path.lexists(Path(eval_dir) / check["overlay"] / PYTHON_DIR)
                        or any(Path(rel).parts[0] == PYTHON_DIR for _, rel in files)):
                    raise repo_tests.RepoTestsConfigError(
                        f"overlay carries reserved {PYTHON_DIR} path")
            except repo_tests.RepoTestsConfigError as exc:
                raise guidance.GuidanceError(
                    f"{where}: {key} check {check.get('id')!r} (repo_tests): "
                    f"{exc}") from None
