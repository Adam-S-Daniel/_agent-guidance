"""registry_edit.py: small edits to a registry checkout, shared by
retire_skill.py and improve_gate.py. No network, no credential."""
import json
import os
import re
import subprocess

SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


def runs_code(plugin_dir: str) -> str | None:
    """Why this plugin counts as one that runs code, or None."""
    for d in ("hooks", "bin"):
        if os.path.isdir(os.path.join(plugin_dir, d)):
            return f"has {d}/"
    try:
        with open(os.path.join(plugin_dir, ".claude-plugin", "plugin.json"), encoding="utf-8") as f:
            if "hooks" in json.load(f):
                return "plugin.json names hooks"
    except (OSError, ValueError):
        pass
    return None


def bumped(version: str, part: str) -> str:
    m = SEMVER.match(version)
    if not m:
        raise ValueError("plugin version is not MAJOR.MINOR.PATCH")
    major, minor, patch = (int(x) for x in m.groups())
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError("part must be major, minor or patch")


def plugin_json_path(registry: str, plugin: str) -> str:
    return os.path.join(registry, "plugins", plugin, ".claude-plugin", "plugin.json")


def read_version(registry: str, plugin: str) -> str:
    with open(plugin_json_path(registry, plugin), encoding="utf-8") as f:
        return json.load(f)["version"]


def bump_plugin(registry: str, plugin: str, part: str) -> tuple:
    """Rewrite plugin.json with the bumped version; return (old, new)."""
    path = plugin_json_path(registry, plugin)
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    old = doc["version"]
    doc["version"] = bumped(old, part)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")
    return old, doc["version"]


def append_impact(registry: str, entry: str) -> None:
    path = os.path.join(registry, "docs", "skill-impact.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    existing = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            existing = f.read()
    sep = "" if not existing else ("\n" if existing.endswith("\n") else "\n\n")
    with open(path, "a", encoding="utf-8") as f:
        f.write(sep + ("\n" if existing else "") + entry.rstrip("\n") + "\n")


def git(registry: str, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", registry, "-c", "user.name=loop-writer",
         "-c", "user.email=loop-writer@users.noreply.example.com", *args],
        capture_output=True, check=True, text=True).stdout
