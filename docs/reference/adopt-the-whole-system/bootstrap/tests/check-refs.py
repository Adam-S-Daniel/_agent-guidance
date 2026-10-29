#!/usr/bin/env python3
"""check-refs.py: every `vars.NAME` and `secrets.NAME` an exported workflow
reads is set by a bootstrap script, or listed as set by hand in
bootstrap/README.md, and every name a script sets is read somewhere.

The script-set names come from lib/common.sh's registries, read by sourcing
it in bash (never by parsing it): APPS (each App's client id, as a variable
or a secret, at repository level, and its key secret, at repository level or
in an environment), OTHER_VALUES (set-secrets.sh) and REPO_VARIABLES
(create-environments.sh). The by-hand names are the list items
"- `NAME`: ..." in the README section "Variables and secrets set by hand".

Workflows are starter/<tree>/.github/workflows/*.yml for each tree that
lib/common.sh's kit_tree maps a fleet repo to, plus the workflow templates
_agent-guidance's sync delivers (starter/agent-guidance/templates/**/*.yml,
which may land in any fleet repo). Each workflow is parsed with PyYAML, so a
name in a comment does not count; a name is a lexical token in the parsed
job (vars|secrets).NAME.

Also checked: an environment's value is read only in a job that runs in that
environment, in the repo that holds it; a repository-level value only in its
own repo; and the README lists no name a script already sets, nor one
nothing reads. Prints "N assertions passed" and exits 1 on any failure.
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


def bash(expr):
    return subprocess.run(["bash", "-c", f'. "$1/lib/common.sh"; {expr}', "_", str(B)],
                          capture_output=True, text=True, check=True).stdout


def registry(name):
    return [line.split("|") for line in bash(f'printf "%s\\n" "${{{name}[@]}}"').splitlines() if line]


FLEET = bash('printf "%s\\n" "${FLEET_REPOS[@]}"').split()
TREE = {r: bash(f'kit_tree {r}').strip() for r in FLEET}

# (kind, name) -> set of (repo, environment); environment None = repository level
SET_BY = {}
for app, _perms, _inst, repo, idspec, env, sec, _key, _opt in registry("APPS"):
    idkind, idname = idspec.split(":", 1)
    SET_BY.setdefault(("vars" if idkind == "variable" else "secrets", idname), set()).add((repo, None))
    SET_BY.setdefault(("secrets", sec), set()).add((repo, None if env == "-" else env))
for repo, env, kind, name, _src in registry("OTHER_VALUES"):
    SET_BY.setdefault(("vars" if kind == "variable" else "secrets", name), set()).add((repo, None if env == "-" else env))
for repo, name, _src, _group in registry("REPO_VARIABLES"):
    SET_BY.setdefault(("vars", name), set()).add((repo, None))
check("every registry repo is a fleet repo", all(r in FLEET for v in SET_BY.values() for r, _ in v))
envs = {(r, e) for r, e, _rev, _br in registry("ENVIRONMENTS")}
check("every environment a value lives in is in ENVIRONMENTS",
      all(e is None or (r, e) in envs for v in SET_BY.values() for r, e in v))

readme = (B / "README.md").read_text(encoding="utf-8")
m = re.search(r"^## Variables and secrets set by hand\n(.*?)(?=^## |\Z)", readme, re.M | re.S)
check("bootstrap/README.md has the section 'Variables and secrets set by hand'", m is not None)
MANUAL = set(re.findall(r"^- `([A-Z][A-Z0-9_]{2,})`", m.group(1), re.M)) if m else set()


def workflows():
    for repo in FLEET:
        for path in sorted((KIT / "starter" / TREE[repo] / ".github" / "workflows").glob("*.yml")):
            yield repo, path
    for path in sorted((KIT / "starter" / "agent-guidance" / "templates").rglob("*.yml")):
        yield "*", path


used, n_files = set(), 0
for repo, path in workflows():
    n_files += 1
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rel = path.relative_to(KIT)
    top = {k: v for k, v in doc.items() if k != "jobs"}
    scopes = [(None, top)] + [(job.get("environment"), job) for job in (doc.get("jobs") or {}).values()]
    for env, node in scopes:
        if isinstance(env, dict):
            env = env.get("name")
        for kind, name in TOKEN.findall(yaml.safe_dump(node)):
            if name == "GITHUB_TOKEN":
                continue
            used.add((kind, name))
            where = SET_BY.get((kind, name))
            if where is None:
                check(f"{rel}: {kind}.{name} is set by a bootstrap script or listed as by hand", name in MANUAL)
                continue
            fits = [r for r, e in where if (e is None or e == env) and (repo == "*" or r == repo)]
            check(f"{rel}: {kind}.{name} is read where a script puts it "
                  f"({', '.join(f'{r}/{e or 'repository'}' for r, e in sorted(where, key=str))}; "
                  f"this job: {repo}/{env or 'no environment'})", bool(fits))
check(f"the scan read the exported workflows ({n_files} files)", n_files >= 40)
for key in sorted(SET_BY):
    check(f"a script sets {key[0]}.{key[1]}, and some workflow reads it", key in used)
for name in sorted(MANUAL):
    check(f"README lists {name} as by hand, but a script sets it", not any(n == name for _, n in SET_BY))
    check(f"README lists {name} as by hand, but no workflow reads it", any(n == name for _, n in used))

print(f"{passed} assertions passed, {failed} failed")
sys.exit(1 if failed else 0)
