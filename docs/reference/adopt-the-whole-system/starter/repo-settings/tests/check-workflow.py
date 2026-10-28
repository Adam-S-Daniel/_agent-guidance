#!/usr/bin/env python3
"""check-workflow.py: shape checks on .github/workflows/apply.yml, parsed with
PyYAML (never a regex). Prints "N assertions passed, M failed" and exits 1 on
any failure.

The workflow re-applies rulesets/ on a push that changes them, weekly and on
dispatch; reads with contents: read only; mints its token from the
settings-admin environment (SETTINGS_ADMIN_CLIENT_ID, SETTINGS_ADMIN_PEM);
pins every action to a full SHA or the kit's placeholder; never puts a ${{ }}
expression inside a run: block; and runs this repo's bin/apply-rulesets.sh on
this repo's rulesets/."""
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


with open(os.path.join(ROOT, ".github", "workflows", "apply.yml"), encoding="utf-8") as f:
    w = yaml.safe_load(f)
on = w.pop(True, w.get("on"))   # PyYAML reads the key `on` as True
check("triggers are push, schedule and workflow_dispatch", set(on) == {"push", "schedule", "workflow_dispatch"})
check("push is main only", on["push"].get("branches") == ["main"])
check("push filters on rulesets/, bin/ and the workflow itself",
      set(on["push"].get("paths", [])) == {"rulesets/**", "bin/**", ".github/workflows/apply.yml"})
check("a weekly schedule", len(on["schedule"]) == 1 and
      re.fullmatch(r"\d{1,2} \d{1,2} \* \* [0-6]", on["schedule"][0]["cron"]) is not None)
check("workflow permissions are contents: read", w["permissions"] == {"contents": "read"})
check("runs queue, never cancel", w.get("concurrency", {}).get("cancel-in-progress") is False)
check("one job, apply", list(w["jobs"]) == ["apply"])
job = w["jobs"]["apply"]
check("the job widens no permission", "permissions" not in job)
check("the job runs in the settings-admin environment", job.get("environment") == "settings-admin")
check("the job has a timeout", isinstance(job.get("timeout-minutes"), int))
steps = job["steps"]
for s in steps:
    if "uses" in s:
        ref = s["uses"].split("@", 1)[-1]
        check(f"{s['uses']}: a full sha or the kit's placeholder",
              ref == PLACEHOLDER or re.fullmatch(r"[0-9a-f]{40}", ref) is not None)
    if "run" in s:
        check(f"{s.get('name', 'a step')}: no expression inside run:", "${{" not in s["run"])
checkout = next(s for s in steps if s.get("uses", "").startswith("actions/checkout@"))
check("checkout keeps no credentials", checkout.get("with", {}).get("persist-credentials") is False)
mint = next(s for s in steps if s.get("uses", "").startswith("actions/create-github-app-token@"))
check("the App token comes from SETTINGS_ADMIN_CLIENT_ID and SETTINGS_ADMIN_PEM",
      mint["with"].get("client-id") == "${{ vars.SETTINGS_ADMIN_CLIENT_ID }}"
      and mint["with"].get("private-key") == "${{ secrets.SETTINGS_ADMIN_PEM }}")
holders = [s.get("name", s.get("uses")) for s in steps if "SETTINGS_ADMIN_PEM" in str(s.get("with", "")) + str(s.get("env", ""))]
check("only the mint step and the presence check name the key", len(holders) == 2)
apply = steps[-1]
check("the last step runs bin/apply-rulesets.sh on rulesets/",
      "bin/apply-rulesets.sh" in apply["run"] and "--rulesets-dir rulesets" in apply["run"])
check("the last step uses the minted token", apply["env"].get("GH_TOKEN") == "${{ steps.token.outputs.token }}")
check("the required-check choice comes from the REQUIRED_CHECKS variable, written into fleet.env",
      apply["env"].get("REQUIRED_CHECKS") == "${{ vars.REQUIRED_CHECKS }}" and "REQUIRED_CHECKS=%s" in apply["run"])
check("the workflow passes no --with/--without-required-check flag (the key decides)",
      "required-check" not in apply["run"])
check("bin/apply-rulesets.sh exists", os.path.isfile(os.path.join(ROOT, "bin", "apply-rulesets.sh")))

print(f"{passed} assertions passed, {failed} failed")
sys.exit(1 if failed else 0)
