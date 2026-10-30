"""Every file of the kit is tracked by git, so a clone gets the kit whole.

The repo's root .gitignore carries generic rules (`**/[Bb]in/*`, `*.log`)
that once kept ten exported files out of git: a seed's `bin/gh` links, the
registry's `bin/claude-credit`, a fake-gh replay `.log`. The disk looked
complete and the suites passed there, while a clone failed them. This test
compares the files on disk under the kit with `git ls-files`, so an ignored
or never-added file fails here, with its path. Test-run junk
(`__pycache__`, `.pytest_cache`, `node_modules`, `*.pyc`) is not kit content
and is skipped. Outside a git work tree (a kit copied out of its repo) there
is nothing to compare, and the test skips.

Run: python3 -m pytest KIT/export/tests -q
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parents[2]
JUNK_DIRS = {"__pycache__", ".pytest_cache", "node_modules", ".git"}


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True)


def disk_files(root: Path) -> set[str]:
    out = set()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in JUNK_DIRS]
        here = Path(dirpath)
        names = list(filenames) + [d for d in dirnames if (here / d).is_symlink()]
        for name in names:
            if name.endswith(".pyc"):
                continue
            out.add((here / name).relative_to(root).as_posix())
    return out


def untracked_files(root: Path) -> list[str] | None:
    """Files on disk under root that git does not track; None outside a work tree."""
    if _git(root, "rev-parse", "--is-inside-work-tree").returncode != 0:
        return None
    listed = _git(root, "ls-files", "-z", "--full-name", "--", ".")
    prefix = _git(root, "rev-parse", "--show-prefix").stdout.decode().strip()
    tracked = {p.decode("utf-8")[len(prefix):] for p in listed.stdout.split(b"\0") if p}
    return sorted(disk_files(root) - tracked)


def test_every_kit_file_is_tracked():
    missing = untracked_files(KIT)
    if missing is None:
        pytest.skip("the kit is not in a git work tree")
    assert not missing, (
        "files under the kit that git does not track (ignored by a .gitignore rule, "
        "or never added), so a clone would not have them:\n  " + "\n  ".join(missing))


def test_an_ignored_file_is_reported(tmp_path):
    repo = tmp_path / "repo"
    kit = repo / "kit" / "seed" / "bin"
    kit.mkdir(parents=True)
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=env)
    (repo / ".gitignore").write_text("**/[Bb]in/*\n*.log\n", encoding="utf-8")
    (kit / "gh").write_text("#!/bin/sh\n", encoding="utf-8")
    (repo / "kit" / "replay.log").write_text("x\n", encoding="utf-8")
    (repo / "kit" / "README.md").write_text("x\n", encoding="utf-8")
    (repo / "kit" / "__pycache__").mkdir()
    (repo / "kit" / "__pycache__" / "a.pyc").write_bytes(b"\0")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, env=env)
    assert untracked_files(repo / "kit") == ["replay.log", "seed/bin/gh"]
    (repo / ".gitignore").write_text("**/[Bb]in/*\n*.log\n!/kit/**/[Bb]in/*\n!/kit/**/*.log\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, env=env)
    assert untracked_files(repo / "kit") == []


def _kit_is_at_its_place_in(root: Path) -> bool:
    top = _git(root, "rev-parse", "--show-toplevel")
    return (top.returncode == 0 and Path(top.stdout.decode().strip()).resolve() == root.resolve()
            and root.resolve() / "docs" / "reference" / "adopt-the-whole-system" == KIT.resolve())


def test_the_kit_carries_its_repos_license():
    """The README links LICENSE inside the kit, so a copied-out kit keeps it.

    Inside the repo it came from, the copy must stay that repo's license.
    """
    assert (KIT / "LICENSE").is_file()
    root = KIT.parents[2] if len(KIT.parents) > 2 else None
    # Only the repo the kit came in has a LICENSE worth comparing: its root is a
    # git work tree and holds this kit at its place. An unrelated LICENSE two
    # folders up from a copy is not that.
    if root is None or not _kit_is_at_its_place_in(root):
        pytest.skip("the kit is not inside the repo it came from")
    repo_license = root / "LICENSE"
    if not repo_license.is_file():
        pytest.skip("the kit is not inside the repo it came from")
    assert (KIT / "LICENSE").read_bytes() == repo_license.read_bytes()


def _export_module():
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location("kit_export_tracked", KIT / "export" / "export.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("kit_export_tracked", mod)
    spec.loader.exec_module(mod)
    return mod


def test_check_reads_what_git_tracks_not_the_disk(tmp_path):
    export = _export_module()
    repo = tmp_path / "repo"
    tree = repo / "starter" / "t"
    (tree / "seed" / "bin").mkdir(parents=True)
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=env)
    (repo / ".gitignore").write_text("**/[Bb]in/*\n", encoding="utf-8")
    (tree / "a.txt").write_text("staged\n", encoding="utf-8")
    (tree / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (tree / "run.sh").chmod(0o755)
    (tree / "seed" / "bin" / "gh").symlink_to("../../a.txt")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, env=env)
    (tree / "a.txt").write_text("edited, not staged\n", encoding="utf-8")
    committed = export.read_index_tree(tree)
    assert set(committed) == {"a.txt", "run.sh"}   # the ignored link is on disk only
    assert committed["a.txt"].data == b"staged\n" and committed["run.sh"].executable
    fresh = export.read_dir_tree(tree)
    problems = export.diff_trees(committed, fresh)
    assert "only in the fresh export: seed/bin/gh" in problems
    assert "content differs: a.txt" in problems
    assert export.diff_trees({"gone.txt": export.Entry(data=b"x")}, {}) == ["only in the committed tree: gone.txt"]
    assert export.read_index_tree(tmp_path / "nowhere" / "t") is None


def test_a_copy_in_a_shallow_folder_still_imports():
    """KIT.parents[3] / [2] raised IndexError at import in a copy near the filesystem root.

    The copy lives directly under /tmp (two ancestors), so the module-level
    lookups would fail; every entry point must still start, and export.py must
    say it has no sources instead of crashing.
    """
    import shutil
    import sys
    import tempfile
    base = Path("/tmp")
    if not base.is_dir() or not os.access(base, os.W_OK):
        pytest.skip("/tmp is not available for a shallow copy")
    copy = Path(tempfile.mkdtemp(dir=base, prefix="kit-shallow-"))
    try:
        assert len(copy.parents) <= 3
        (copy / "export").mkdir()
        for script in (KIT / "export").glob("*.py"):   # the entry points only; the overlay holds dangling links
            shutil.copy2(script, copy / "export" / script.name)
        env = {k: v for k, v in os.environ.items() if k != "KIT_EXPORT_SOURCES"}
        py = [sys.executable, "-B"]
        imp = subprocess.run([*py, "-c", "import sys; sys.path.insert(0, 'export'); import export; "
                              "assert export.DEFAULT_SOURCES is None"],
                             cwd=copy, capture_output=True, text=True, env=env)
        assert imp.returncode == 0, imp.stderr
        chk = subprocess.run([*py, "export/export.py", "--check"], cwd=copy, capture_output=True, text=True, env=env)
        assert chk.returncode == 2 and "no source checkouts" in chk.stderr, (chk.returncode, chk.stderr)
        for script in ("run_suites.py", "fetch_sources.py"):
            h = subprocess.run([*py, f"export/{script}", "--help"], cwd=copy, capture_output=True, text=True, env=env)
            assert h.returncode == 0, (script, h.stderr)
    finally:
        shutil.rmtree(copy, ignore_errors=True)


def test_the_real_root_gitignore_clears_everything_under_the_kit(tmp_path):
    """The repo's own .gitignore (VisualStudio rules and all) drops nothing under the kit but junk."""
    top = _git(KIT, "rev-parse", "--show-toplevel")
    root = Path(top.stdout.decode().strip()) if top.returncode == 0 else None
    if root is None or not _kit_is_at_its_place_in(root) or not (root / ".gitignore").is_file():
        pytest.skip("the kit is not inside the repo it came from")
    rel = "docs/reference/adopt-the-whole-system"
    repo = tmp_path / "repo"
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=env)
    (repo / ".gitignore").write_bytes((root / ".gitignore").read_bytes())
    kept = ["starter/x/Logs/a.txt", "starter/x/log/a.txt", "out/a", "artifacts/a", "x64/a", "x86/a", "Debug/a",
            "Release/a", "obj/a", "bin/a", "seed/bin/gh", "a.log", "Build/a", "starter/x/TestResults/a"]
    junk = ["x/__pycache__/a.pyc", "a.pyc", "x/node_modules/p/i.js", "x/.pytest_cache/v"]
    for name in kept + junk:
        path = repo / rel / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, env=env)
    tracked = {p.decode() for p in _git(repo, "ls-files", "-z").stdout.split(b"\0") if p}
    assert {f"{rel}/{n}" for n in kept} - tracked == set(), "kit files the root .gitignore still drops"
    assert not [t for t in tracked if "__pycache__" in t or t.endswith(".pyc") or "node_modules" in t or ".pytest_cache" in t]
