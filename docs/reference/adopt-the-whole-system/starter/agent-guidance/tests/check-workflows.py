#!/usr/bin/env python3
"""check-workflows.py: shape checks on the workflows this repo ships, parsed
with PyYAML (never a regex). Prints "N assertions passed" and exits 1 on any
failure.

templates/agent-checks.yml (guide 6.3) holds the required checks, so: it runs
on pull_request_target and push to main, has no path filter and no concurrency
group (3.2), reads with contents: read only, never scans a fork's change but
fails it in the required privacy context, and interpolates no ${{ }}
expression into a run: block."""
import os
import re
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLACEHOLDER = "<sha-of-the-v-tag-you-chose>"   # replace with the tag's commit before use
passed, failed = 0, 0


def check(desc, cond):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print("FAIL:", desc)


def load(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    doc["on"] = doc.pop(True, doc.get("on"))   # PyYAML reads the key `on` as True
    return doc


w = load("templates/agent-checks.yml")
on = w["on"]
check("agent-checks: triggers are pull_request_target and push", set(on) == {"pull_request_target", "push"})
check("agent-checks: push is main only", on["push"] == {"branches": ["main"]})
check("agent-checks: no path filter on any trigger",
      all(not isinstance(v, dict) or not ({"paths", "paths-ignore"} & set(v)) for v in on.values()))
check("agent-checks: no concurrency group", "concurrency" not in w and
      all("concurrency" not in j for j in w["jobs"].values()))
check("agent-checks: contents: read", w["permissions"] == {"contents": "read"})
check("agent-checks: jobs are agent-checks, privacy-scan and privacy",
      set(w["jobs"]) == {"agent-checks", "privacy-scan", "privacy"})
scan, rep = w["jobs"]["privacy-scan"], w["jobs"]["privacy"]
check("agent-checks: the scan never runs for a fork",
      "head.repo.full_name == github.repository" in scan["if"])
check("agent-checks: the scan never runs on a private repo",
      scan["if"].lstrip().startswith("!github.event.repository.private &&"))
check("agent-checks: the scan reads its secret from an environment", scan["environment"] == "privacy")
check("agent-checks: privacy (the required context) always runs after the scan, with no secret",
      rep.get("needs") == "privacy-scan" and rep.get("if") == "always()" and "environment" not in rep
      and "secrets." not in yaml.safe_dump(rep))
check("agent-checks: privacy fails a fork's pull request with the fixed message",
      "bring this change in through a branch of your own" in rep["steps"][-1]["run"]
      and "exit 1" in rep["steps"][-1]["run"])
for name, job in w["jobs"].items():
    for s in job["steps"]:
        check(f"agent-checks/{name}: no expression inside run:", "${{" not in s.get("run", ""))
        if "uses" in s:
            ref = s["uses"].split("@", 1)[-1]
            check(f"agent-checks/{name}: uses is a full sha or the guide's placeholder",
                  ref == PLACEHOLDER or re.fullmatch(r"[0-9a-f]{40}", ref))
runner_steps = [s for s in w["jobs"]["agent-checks"]["steps"] if "run" in s]
check("agent-checks: runs the base branch's runner, read with git show",
      any('git show "$BASE:.githooks/agent-checks"' in s["run"] for s in runner_steps))

s = load(".github/workflows/sync.yml")
check("sync: contents: read", s["permissions"] == {"contents": "read"})
check("sync: queues, never cancels", s["concurrency"] == {"group": "sync", "cancel-in-progress": False})

d = load(".github/workflows/drift-report.yml")
check("drift-report: nightly and by hand only", set(d["on"]) == {"schedule", "workflow_dispatch"})
check("drift-report: contents: read", d["permissions"] == {"contents": "read"})
dj = d["jobs"]["report"]
check("drift-report: the guidance-sync environment", dj["environment"] == "guidance-sync")
dsteps = dj["steps"]
mint = [i for i, s in enumerate(dsteps) if s.get("uses", "").startswith("actions/create-github-app-token@")]
users = [i for i, s in enumerate(dsteps) if "steps.token.outputs.token" in yaml.safe_dump(s)]
check("drift-report: the token is minted in the step right before its one use", len(mint) == 1 and users == [mint[0] + 1])
check("drift-report: runs drift-report.sh --push", "scripts/drift-report.sh --push" in dsteps[-1]["run"])
check("drift-report: no ${{ }} in run:", all("${{" not in s.get("run", "") for s in dsteps))
check("drift-report: every uses: is the placeholder or a full SHA",
      all(re.search(r"@([0-9a-f]{40}|" + re.escape(PLACEHOLDER) + r")$", s["uses"]) for s in dsteps if "uses" in s))

print(f"{passed} assertions passed, {failed} failed")
sys.exit(1 if failed else 0)
