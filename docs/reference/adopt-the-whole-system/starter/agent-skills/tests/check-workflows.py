#!/usr/bin/env python3
"""check-workflows.py: shape checks for agent-skills' own workflows.

loop-disarm.yml runs from the default branch on every push to a loop pull
request (pull_request_target, synchronize), covers exactly the lanes that
open pull requests here, and keeps every expression out of its run: block.
plugin-runtime-review.yml is a required check that always reports, and asks
for the owner's approval only when detect finds a runtime change.
Run it: python3 tests/check-workflows.py (needs PyYAML). Exit 0 and "N
assertions passed" mean it worked.
"""
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / ".github" / "workflows"
passed = failed = 0


def check(desc, ok):
    global passed, failed
    if ok:
        passed += 1
    else:
        failed += 1
        print(f"FAIL: {desc}")


def load(name):
    doc = yaml.safe_load((WF / name).read_text())
    doc["on"] = doc.pop(True, doc.get("on"))
    return doc


def runs(doc):
    for job in doc["jobs"].values():
        for step in job.get("steps", []):
            yield step.get("run", "")


d = load("loop-disarm.yml")
check("loop-disarm: pull_request_target synchronize only", d["on"] == {"pull_request_target": {"types": ["synchronize"]}})
check("loop-disarm: pull-requests: write and nothing else", d["permissions"] == {"pull-requests": "write"})
cond = d["jobs"]["disarm"]["if"]
for prefix in ("eval-improve/", "eval-draft/", "retire-skill/"):
    check(f"loop-disarm: covers {prefix}", f"'{prefix}'" in cond)
check("loop-disarm: not eval-scaffold/ (those PRs are in skills-evals)", "eval-scaffold/" not in cond)
check("loop-disarm: no ${{ }} in run:", all("${{" not in r for r in runs(d)))
check("loop-disarm: the repository comes in through env", d["jobs"]["disarm"]["steps"][0]["env"].get("REPO") == "${{ github.repository }}")

r = load("plugin-runtime-review.yml")
check("runtime review: pull_request_target only, no paths filter",
      list(r["on"]) == ["pull_request_target"] and "paths" not in (r["on"]["pull_request_target"] or {}))
check("runtime review: contents: read", r["permissions"] == {"contents": "read"})
check("runtime review: no concurrency group (it is a required check)",
      "concurrency" not in r and all("concurrency" not in j for j in r["jobs"].values()))
check("runtime review: no ${{ }} in run:", all("${{" not in x for x in runs(r)))
jobs = r["jobs"]
check("runtime review: detect, approval, and the reporting job", set(jobs) == {"detect", "plugin-runtime-approval", "plugin-runtime-review"})
check("runtime review: approval waits in the owner's environment",
      jobs["plugin-runtime-approval"]["environment"] == "plugin-runtime-review")
check("runtime review: approval runs only on a runtime change",
      jobs["plugin-runtime-approval"]["if"] == "needs.detect.outputs.runtime == 'true'")
rep = jobs["plugin-runtime-review"]
check("runtime review: the reporting job always runs", rep["if"] == "always()" and set(rep["needs"]) == {"detect", "plugin-runtime-approval"})
det = jobs["detect"]["steps"]
check("runtime review: detect runs the base branch's script", "scripts/runtime_detect.py" in det[-1]["run"]
      and "pr/head" not in det[-1]["run"] and "ref" not in det[0].get("with", {}))
check("runtime review: detect's checkout keeps no credential", det[0]["with"]["persist-credentials"] is False)

# The reporting job's decision, run for every outcome.
import subprocess
script = rep["steps"][0]["run"]
def outcome(detect, runtime, approval):
    env = {"DETECT": detect, "RUNTIME": runtime, "APPROVAL": approval, "PATH": "/usr/bin:/bin"}
    return subprocess.run(["bash", "-c", script], env=env, capture_output=True).returncode
check("report: not runtime, approval skipped -> pass", outcome("success", "false", "skipped") == 0)
check("report: runtime, approved -> pass", outcome("success", "true", "success") == 0)
check("report: runtime, approval waiting or failed -> fail",
      outcome("success", "true", "failure") == 1 and outcome("success", "true", "cancelled") == 1)
check("report: runtime, approval skipped -> fail", outcome("success", "true", "skipped") == 1)
check("report: detect failed -> fail", outcome("failure", "", "skipped") == 1)
check("report: detect said nothing -> fail", outcome("success", "", "skipped") == 1)

print(f"{passed} assertions passed, {failed} failed")
sys.exit(1 if failed else 0)
