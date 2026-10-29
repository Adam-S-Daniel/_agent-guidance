"""Shared check that a workflow's `uses:` pin goes through actions.lock.

Third-party actions are named by release tag, and
`.github/workflows/actions.lock` locks each tag to an exact commit (ADR 0024
in example-adopter/_agent-guidance). A local step `uses:` (`./`, `$/`) needs
no lock. A `docker://` ref is not in the lockfile, so it must be pinned by
digest (`@sha256:<64 lowercase hex>`). An `example-adopter/example-site-platform/` ref
must name an exact release tag (`@vX.Y.Z`). Any other `uses:`
must have a lockfile entry keyed by the exact `uses:` string,
whose `commit` is `sha1-<40 lowercase hex>`, whose `ref` is the text after the
last `@`, and which the lockfile's `workflows` list for that file names.

The lockfile is read with PyYAML, never a line scan.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCKFILE = REPO_ROOT / ".github" / "workflows" / "actions.lock"
LOCKED_COMMIT = re.compile(r"sha1-[0-9a-f]{40}")
LOCAL_PREFIXES = ("./", "$/")
DOCKER_PREFIX = "docker://"
DOCKER_DIGEST = re.compile(r"@sha256:[0-9a-f]{64}\Z")
EXAMPLE_SITE_PLATFORM_PREFIX = "example-adopter/example-site-platform/"
EXAMPLE_SITE_PLATFORM_TAG = re.compile(r"@v[0-9]+\.[0-9]+\.[0-9]+\Z")


def load_lock(path: Path = LOCKFILE) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def lock_problem(uses: str, workflow: Path | str, lock: dict | None = None) -> str | None:
    """Why `uses` in `workflow` is not locked, or None when it is (or needs no lock)."""
    if uses.startswith(LOCAL_PREFIXES):
        return None
    if uses.startswith(DOCKER_PREFIX):
        if DOCKER_DIGEST.search(uses):
            return None
        return f"{uses!r} is a docker image not pinned by @sha256:<64 lowercase hex> digest"
    if uses.startswith(EXAMPLE_SITE_PLATFORM_PREFIX):
        if EXAMPLE_SITE_PLATFORM_TAG.search(uses):
            return None
        return f"{uses!r} is a example-site-platform ref not pinned to an exact vX.Y.Z release tag"
    lock = load_lock() if lock is None else lock
    key = f".github/workflows/{Path(workflow).name}"
    # The tool lowercases owner and repo in its keys (GitHub names are case-insensitive).
    name, at, tag = uses.rpartition("@")
    lock_key = name.lower() + at + tag
    entry = (lock.get("dependencies") or {}).get(lock_key)
    if not isinstance(entry, dict):
        return f"{uses!r} has no actions.lock dependencies entry"
    commit = entry.get("commit")
    if not isinstance(commit, str) or not LOCKED_COMMIT.fullmatch(commit):
        return f"{uses!r} locks commit {commit!r}, not sha1-<40 lowercase hex>"
    if entry.get("ref") != uses.rsplit("@", 1)[-1]:
        return f"{uses!r} locks ref {entry.get('ref')!r}, not the tag it names"
    if lock_key not in ((lock.get("workflows") or {}).get(key) or []):
        return f"{uses!r} is missing from actions.lock's workflows[{key!r}]"
    return None
