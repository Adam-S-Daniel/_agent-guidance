#!/usr/bin/env python3
"""check-one-owner.py: a one-owner fleet's substituted YAML has no duplicates.

    check-one-owner.py [--no-collapse]

A one-owner fleet sets OWNER_TWO to OWNER, so lib/substitute.py maps both
invented owners to one login. Without its collapse step, the second owner's
legs would become exact copies of the owner's: duplicate env keys
(GH_TOKEN_<OWNER>), duplicate step ids, an owner listed twice in a matrix
or in SYNC_OWNERS. This copies every .yml/.yaml file of every starter tree
(starter/*/), substitutes it with --owner acme-solo --owner-two ACME-Solo,
and loads each result with a loader that rejects a duplicate mapping key,
then checks that no job repeats a step id, no flow list repeats an owner,
and no one-line space-separated owner list names the owner twice (a block
scalar's text, such as a `run:` script, is prose and code, not a list).

--no-collapse runs the same check with the collapse step turned off, which
must FAIL: it is the proof this check can fail. Prints "N assertions passed"
and exits 1 on any failure.
"""
import contextlib
import io
import pathlib
import shutil
import sys
import tempfile

import yaml

B = pathlib.Path(__file__).resolve().parent.parent
KIT = B.parent
sys.path.insert(0, str(B / "lib"))
import substitute  # noqa: E402

OWNER = "acme-solo"
passed = failed = 0


def check(desc, ok):
    global passed, failed
    if ok:
        passed += 1
    else:
        failed += 1
        print(f"FAIL: {desc}")


class UniqueKeys(yaml.SafeLoader):
    pass


def _mapping(loader, node, deep=False):
    seen = set()
    for k, _ in node.value:
        key = loader.construct_object(k, deep=deep)
        if key in seen:
            raise yaml.constructor.ConstructorError(None, None, f"duplicate key {key!r}", k.start_mark)
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep)


UniqueKeys.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def walk(node, rel):
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, str) and "\n" not in v and OWNER in v.split(" "):
                words = v.split(" ")
                check(f"{rel}: {k} names the owner once", words.count(OWNER) == 1)
            walk(v, rel)
    elif isinstance(node, list):
        owners = [x for x in node if isinstance(x, str) and x == OWNER]
        check(f"{rel}: a list names the owner at most once", len(owners) <= 1)
        for x in node:
            walk(x, rel)


def main(argv):
    collapse = "--no-collapse" not in argv
    if not collapse:
        substitute.collapse_two = lambda root: None
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="one-owner-"))
    try:
        n_files = n_two = 0
        for tree in sorted(p for p in (KIT / "starter").iterdir() if p.is_dir()):
            for src in sorted(tree.rglob("*.y*ml")):
                if src.suffix not in (".yml", ".yaml") or "node_modules" in src.parts:
                    continue
                dst = tmp / tree.name / src.relative_to(tree)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
                n_two += any(f in src.read_text(encoding="utf-8", errors="replace") for f in substitute.TWO_FORMS)
        for tree in sorted(tmp.iterdir()):
            log = io.StringIO()
            try:
                with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                    substitute.main([str(tree), "--owner", OWNER, "--owner-two", "ACME-Solo", "--name", "Acme Solo"])
            except SystemExit as e:
                ok = e.code in (0, None)
                check(f"substitute.py fills {tree.name} with one owner (exit {e.code})", ok)
                if not ok:
                    print(log.getvalue()[-2000:])
        check(f"the YAML files that name the second owner were found ({n_two})", n_two >= 7)
        for path in sorted(tmp.rglob("*.y*ml")):
            rel = path.relative_to(tmp)
            n_files += 1
            text = path.read_text(encoding="utf-8")
            try:
                docs = list(yaml.load_all(text, Loader=UniqueKeys))
            except yaml.YAMLError as e:
                check(f"{rel} loads with no duplicate key ({str(e).splitlines()[0]})", False)
                continue
            check(f"{rel} loads with no duplicate key", True)
            for doc in docs:
                walk(doc, rel)
                jobs = doc.get("jobs") if isinstance(doc, dict) else None
                for name, job in (jobs or {}).items():
                    ids = [s.get("id") for s in (job or {}).get("steps") or [] if isinstance(s, dict) and s.get("id")]
                    check(f"{rel}: job {name} repeats no step id", len(ids) == len(set(ids)))
        check(f"the scan read the starter YAML ({n_files} files)", n_files >= 60)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"{passed} assertions passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
