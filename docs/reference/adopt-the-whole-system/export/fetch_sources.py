#!/usr/bin/env python3
"""fetch_sources.py: fetch the source checkouts export.py reads, at their pinned commits.

Usage:
  python3 fetch_sources.py --owner OWNER --dest DIR [--self PATH] [--skip REPO ...]

For CI (the kit's home repo runs `export.py --check` on a runner that has
only its own checkout). Every manifest's `source` at its `source_sha`, and
every `imports` entry's repo at its `sha`, is fetched into DIR/<source> as a
bare-bones git repository holding just those commits (`git fetch --depth 1
<url> <sha>`, which is all `git archive <sha>` needs), from
https://github.com/OWNER/<source>.git. The repo the kit lives in is not
fetched: DIR/<its name> becomes a symlink to --self (its full checkout,
which also has the node_modules a regenerate step reads). --skip names
manifest repos to leave out, the same names `export.py --skip` takes, for a
source the runner cannot read. No credential is used: every fetched source
must be public.

Prints one line per source, never a URL with a credential (there is none).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import export  # noqa: E402

OWNER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
NAME = re.compile(r"[A-Za-z0-9._-]{1,100}")
SHA = re.compile(r"[0-9a-f]{40}")


def plan(manifests: dict, skip=()) -> dict[str, list[str]]:
    """{source repo name: [pinned shas]} for every manifest not skipped."""
    unknown = sorted(set(skip) - set(manifests))
    if unknown:
        raise export.ExportError(f"no manifest for {' '.join(unknown)}")
    out: dict[str, set] = {}
    for name, m in manifests.items():
        if name in skip:
            continue
        out.setdefault(m.get("source", name), set()).add(m["source_sha"])
        for imp in m.get("imports") or []:
            out.setdefault(imp["repo"], set()).add(imp["sha"])
    for repo, shas in out.items():
        if not NAME.fullmatch(repo) or repo in (".", ".."):
            raise export.ExportError(f"unsafe source repo name {repo!r}")
        bad = [s for s in shas if not SHA.fullmatch(str(s))]
        if bad:
            raise export.ExportError(f"{repo}: not a full sha: {bad}")
    return {repo: sorted(shas) for repo, shas in sorted(out.items())}


def git(*args: str) -> None:
    subprocess.run(["git", *args], check=True, stdout=subprocess.DEVNULL)


def _default_self():
    """The checkout of the repo holding the kit; None in a copy too shallow to have one."""
    return export.KIT.parents[2] if len(export.KIT.parents) > 2 else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--owner", required=True, help="the GitHub owner the sources are fetched from")
    ap.add_argument("--dest", required=True, type=Path, help="the folder to fetch into (export.py --sources)")
    ap.add_argument("--self", dest="self_path", type=Path, default=_default_self(),
                    help="the checkout of the repo holding the kit (default: this one)")
    ap.add_argument("--skip", nargs="*", default=[], help="manifest repo names to leave out")
    args = ap.parse_args(argv)
    if not OWNER.fullmatch(args.owner):
        print(f"fetch_sources.py: not a GitHub owner: {args.owner!r}", file=sys.stderr)
        return 2
    try:
        wanted = plan(export.load_manifests(), args.skip)
    except export.ExportError as exc:
        print(f"fetch_sources.py: {exc}", file=sys.stderr)
        return 2
    if args.self_path is None:
        print("fetch_sources.py: this copy of the kit is not inside the repo it came from; pass --self", file=sys.stderr)
        return 2
    self_path = args.self_path.resolve()
    self_name = export.KIT.parents[2].name
    args.dest.mkdir(parents=True, exist_ok=True)
    for repo, shas in wanted.items():
        target = args.dest / repo
        if repo == self_name:
            if not target.exists():
                target.symlink_to(self_path, target_is_directory=True)
            print(f"{repo}: the kit's own checkout ({len(shas)} pinned commit(s))")
            continue
        if not (target / ".git").exists():
            git("init", "-q", str(target))
        for sha in shas:
            git("-C", str(target), "fetch", "-q", "--depth", "1", "--no-tags",
                f"https://github.com/{args.owner}/{repo}.git", sha)
        print(f"{repo}: fetched {len(shas)} pinned commit(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
