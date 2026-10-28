#!/usr/bin/env python3
"""test-layout.py: the registry's real tree agrees with itself.
  - scripts/plugin_layout.json lists exactly the skill directories under
    plugins/*/skills/, plugin by plugin;
  - each SKILL.md's frontmatter `name:` equals its directory name;
  - .claude-plugin/marketplace.json lists every plugin directory, at its path;
  - .agent-checks/local.sh passes on the real tree (in CI mode, over a scratch
    git copy whose one commit adds every file).
Standard library only. Prints "N assertions passed, M failed"; exit 1 on any
failure. Runs directly: `python3 tests/test-layout.py`."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
passed = failed = 0


def check(desc, ok):
    global passed, failed
    if ok:
        passed += 1
    else:
        failed += 1
        print(f"FAIL: {desc}")


def frontmatter_name(skill_md: Path):
    lines = skill_md.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        return None
    for line in lines[1:]:
        if line == "---":
            return None
        if line.startswith("name:"):
            return line[len("name:"):].strip().strip("'\"")
    return None


plugin_dirs = sorted(p.name for p in (ROOT / "plugins").iterdir() if p.is_dir())
on_disk = {p: sorted(s.name for s in (ROOT / "plugins" / p / "skills").iterdir() if s.is_dir())
           for p in plugin_dirs}
layout = json.loads((ROOT / "scripts" / "plugin_layout.json").read_text())["plugins"]
check("plugin_layout.json names exactly the plugin directories", sorted(layout) == plugin_dirs)
for plugin, skills in on_disk.items():
    listed = layout.get(plugin, {}).get("skills")
    check(f"plugin_layout.json lists exactly {plugin}'s skill directories ({skills}, got {listed})",
          listed == skills)
    for skill in skills:
        md = ROOT / "plugins" / plugin / "skills" / skill / "SKILL.md"
        check(f"{plugin}/{skill} has a SKILL.md", md.is_file())
        if md.is_file():
            name = frontmatter_name(md)
            check(f"{plugin}/{skill}/SKILL.md name: is {name!r}, not the directory name", name == skill)

market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
entries = {e["name"]: e.get("source") for e in market["plugins"]}
check(f"marketplace.json lists every plugin directory ({plugin_dirs}, got {sorted(entries)})",
      sorted(entries) == plugin_dirs)
for plugin in plugin_dirs:
    check(f"marketplace.json source for {plugin} is its directory",
          entries.get(plugin) == f"./plugins/{plugin}")
    manifest = ROOT / "plugins" / plugin / ".claude-plugin" / "plugin.json"
    check(f"{plugin} has a plugin.json naming it",
          manifest.is_file() and json.loads(manifest.read_text()).get("name") == plugin)

# local.sh in CI mode over a scratch copy: base is an empty commit, head adds
# the whole tree, so every SKILL.md line is an added line and every plugin new.
scratch = Path(tempfile.mkdtemp(prefix="layout-"))
try:
    home = scratch / "home"
    home.mkdir()
    (scratch / "gitconfig").write_bytes(b"")
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home),
           "GIT_CONFIG_GLOBAL": str(scratch / "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
           "GIT_AUTHOR_DATE": "1700000000 +0000", "GIT_COMMITTER_DATE": "1700000000 +0000"}
    repo = scratch / "repo"
    shutil.copytree(ROOT, repo, ignore=shutil.ignore_patterns(".git", "__pycache__"))

    def git(*args):
        subprocess.run(["git", "-C", str(repo), *args], env=env, check=True, capture_output=True)

    git("init", "-q")
    git("commit", "-q", "--allow-empty", "-m", "base")
    git("add", "-A")
    git("commit", "-q", "-m", "tree")
    done = subprocess.run(["bash", ".agent-checks/local.sh", "ci", "HEAD~1", "HEAD"], cwd=repo,
                          env=env, capture_output=True, text=True)
    check(f"local.sh passes on the real tree (exit {done.returncode}: {done.stderr.strip()[:300]})",
          done.returncode == 0)
finally:
    shutil.rmtree(scratch, ignore_errors=True)

print(f"{passed} assertions passed, {failed} failed")
sys.exit(1 if failed else 0)
