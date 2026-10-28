#!/usr/bin/env python3
"""Replay a change against the workflows' path filters.

    replay.py --changed <path> [--changed <path> ...] (--expect-skip NAME | --expect-run NAME)

NAME is a workflow file stem under .github/workflows/. A workflow runs on a
pull_request when it has no `paths` / `paths-ignore` filter, when any changed
file matches `paths`, or when not every changed file matches `paths-ignore`.
A workflow that is missing, or that never runs on any trigger, does not
satisfy --expect-run, so deleting a workflow cannot pass.
"""
import argparse
import fnmatch
import pathlib
import re
import sys

import yaml


def glob_match(path: str, pattern: str) -> bool:
    regex = re.escape(pattern).replace(r"\*\*/", "(?:.*/)?").replace(r"\*\*", ".*").replace(r"\*", "[^/]*")
    return re.fullmatch(regex, path) is not None


def runs(workflow: pathlib.Path, changed: list[str]) -> bool:
    doc = yaml.safe_load(workflow.read_text())
    triggers = doc.get("on", doc.get(True))  # PyYAML reads the key `on` as True
    if isinstance(triggers, str):
        triggers = {triggers: None}
    elif isinstance(triggers, list):
        triggers = {t: None for t in triggers}
    if not isinstance(triggers, dict) or "pull_request" not in triggers:
        return False
    cfg = triggers["pull_request"] or {}
    if cfg.get("paths"):
        return any(glob_match(c, p) for c in changed for p in cfg["paths"])
    if cfg.get("paths-ignore"):
        return not all(any(glob_match(c, p) for p in cfg["paths-ignore"]) for c in changed)
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--changed", action="append", required=True)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--expect-skip")
    group.add_argument("--expect-run")
    a = ap.parse_args()
    name = a.expect_skip or a.expect_run
    wf = pathlib.Path(".github/workflows") / f"{name}.yml"
    if not wf.is_file():
        print(f"workflow {name} is missing")
        return 1
    ran = runs(wf, a.changed)
    if a.expect_skip:
        print(f"{name} runs: {ran} (expected skip)")
        return 0 if not ran else 1
    print(f"{name} runs: {ran} (expected run)")
    return 0 if ran else 1


if __name__ == "__main__":
    sys.exit(main())
