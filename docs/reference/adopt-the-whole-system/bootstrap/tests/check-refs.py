#!/usr/bin/env python3
"""check-refs.py: every `vars.NAME` and `secrets.NAME` a kit workflow reads
is set by a bootstrap script, or listed as set by hand in bootstrap/README.md.

The script-set names come from lib/common.sh's registries, read by sourcing
it in bash (never by parsing it): APPS (each App's client-ID variable and key
secret, in its environment), OTHER_VALUES (set-secrets.sh, per environment)
and REPO_VARIABLES (create-environments.sh, repository level). The by-hand
names are the list items "- `NAME`: ..." in the README section "Variables
and secrets set by hand". Workflows are starter/*/.github/workflows/*.yml and the
templates sync.sh delivers (starter/agent-guidance/templates/*.yml, which may
land in any fleet repo). A name is a lexical token (vars|secrets).NAME; the
job it sits in is found by parsing the YAML.

Also checked: an environment's value is read only in a job that runs in that
environment, in a repo that holds it; a repository variable only in its own
repo; and the README lists no name a script already sets, nor one nothing reads.
Prints "N assertions passed" and exits 1 on any failure.
"""
import re
import subprocess
import sys
from pathlib import Path

import yaml

B = Path(__file__).resolve().parent.parent
KIT = B.parent
TOKEN = re.compile(r"\b(vars|secrets)\.([A-Z][A-Z0-9_]*)\b")
passed = failed = 0


def check(desc, ok):
    global passed, failed
    if ok:
        passed += 1
    else:
        failed += 1
        print(f"FAIL: {desc}")


def registry(name):
    out = subprocess.run(["bash", "-c", f'. "$1/lib/common.sh"; printf "%s\\n" "${{{name}[@]}}"', "_", str(B)],
                         capture_output=True, text=True, check=True).stdout
    return [line.split("|") for line in out.splitlines() if line]


# name -> set of (repo, environment); environment None means repository level
SET_BY = {}
for app, _perms, _inst, repo, env, var, sec, _key, _opt in registry("APPS"):
    SET_BY.setdefault(("vars", var), set()).add((repo, env))
    SET_BY.setdefault(("secrets", sec), set()).add((repo, env))
for repo, env, kind, name, _src in registry("OTHER_VALUES"):
    SET_BY.setdefault(("vars" if kind == "variable" else "secrets", name), set()).add((repo, env))
for repo, name, _src in registry("REPO_VARIABLES"):
    SET_BY.setdefault(("vars", name), set()).add((repo, None))

readme = (B / "README.md").read_text(encoding="utf-8")
m = re.search(r"^## Variables and secrets set by hand\n(.*?)(?=^## |\Z)", readme, re.M | re.S)
check("bootstrap/README.md has the section 'Variables and secrets set by hand'", m is not None)
MANUAL = set(re.findall(r"^- `([A-Z][A-Z0-9_]{2,})`", m.group(1), re.M)) if m else set()


def workflows():
    for repo_dir in sorted((KIT / "starter").iterdir()):
        for path in sorted((repo_dir / ".github" / "workflows").glob("*.yml")):
            yield repo_dir.name, path
    for path in sorted((KIT / "starter" / "agent-guidance" / "templates").glob("*.yml")):
        yield "*", path


used = set()
for repo, path in workflows():
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    rel = path.relative_to(KIT)
    top = {k: v for k, v in doc.items() if k != "jobs"}
    scopes = [(None, top)] + [(job.get("environment"), job) for job in doc.get("jobs", {}).values()]
    for env, node in scopes:
        if isinstance(env, dict):
            env = env.get("name")
        for kind, name in TOKEN.findall(yaml.safe_dump(node)):
            if name == "GITHUB_TOKEN":
                continue
            used.add(name)
            where = SET_BY.get((kind, name))
            if where is None:
                check(f"{rel}: {kind}.{name} is set by a bootstrap script or listed as by hand", name in MANUAL)
                continue
            repo_ok = {r for r, e in where if e is None or e == env}
            fits = repo == "*" and repo_ok or any(r == repo for r in repo_ok)
            check(f"{rel}: {kind}.{name} is read where a script puts it "
                  f"({', '.join(f'{r}/{e or 'repository'}' for r, e in sorted(where, key=str))}; "
                  f"this job: {repo}/{env or 'no environment'})", bool(fits))
for name in sorted(MANUAL):
    check(f"README lists {name} as by hand, but a script sets it", not any(n == name for _, n in SET_BY))
    check(f"README lists {name} as by hand, but no workflow reads it", name in used)

print(f"{passed} assertions passed, {failed} failed")
sys.exit(1 if failed else 0)
