#!/usr/bin/env python3
"""runtime_detect.py: does a change alter how a plugin runs code?

    runtime_detect.py --repo DIR --base SHA --head SHA [--github-output FILE]

plugin-runtime-review.yml's detect job runs this from the BASE branch's
checkout, with the pull request's head fetched as data. It reads both commits
through `git diff` and `git show` only; nothing from the head is run. A
change is a runtime change (the approval job must run) when it

  * adds, changes or deletes anything under plugins/<p>/hooks/ or plugins/<p>/bin/;
  * changes plugins/<p>/settings.json (plugin settings);
  * changes a runtime key of plugins/<p>/.claude-plugin/plugin.json: hooks,
    mcpServers, lspServers, or settings (a key added, removed or changed);
  * changes a file that a hook command of that plugin names as
    ${CLAUDE_PLUGIN_ROOT}/<path> (or %PLUGIN_ROOT%/<path>), in the hook
    definitions at the base OR the head, so pointing a hook at a new script
    and editing an old one are both caught.

A plugin.json or hooks.json that does not parse at either commit counts as a
runtime change: unreadable is never "safe". Prints {"runtime": bool,
"reasons": [...]} and, with --github-output, writes runtime=true|false.
Exit 0 decided, 2 bad usage or git failure.
"""
import argparse
import json
import re
import subprocess
import sys

PLUGIN_PATH = re.compile(r"\Aplugins/([^/]+)/(.+)\Z")
RUNTIME_KEYS = ("hooks", "mcpServers", "lspServers", "settings")
# A lexical token inside a hook command: the plugin-root variable and a path.
ROOT_REF = re.compile(r"(?:\$\{CLAUDE_PLUGIN_ROOT\}|\$CLAUDE_PLUGIN_ROOT|%PLUGIN_ROOT%)[\"']?/([A-Za-z0-9._/-]+)")


class Unreadable(Exception):
    pass


def _json(text):
    if text is None:
        return None
    try:
        return json.loads(text)
    except ValueError:
        raise Unreadable() from None


def hook_commands(node):
    """Every command string in a hooks definition, wherever it sits."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ("command", "commandWindows") and isinstance(v, str):
                yield v
            else:
                yield from hook_commands(v)
    elif isinstance(node, list):
        for v in node:
            yield from hook_commands(v)


def hook_targets(plugin, read):
    """Paths (relative to the repo) that this plugin's hook commands name, at one commit."""
    out = set()
    manifest = _json(read(f"plugins/{plugin}/.claude-plugin/plugin.json")) or {}
    defs = [_json(read(f"plugins/{plugin}/hooks/hooks.json"))]
    hooks = manifest.get("hooks") if isinstance(manifest, dict) else None
    if isinstance(hooks, (dict, list)):
        defs.append(hooks)
    for ref in (hooks if isinstance(hooks, list) else [hooks]):
        if isinstance(ref, str):
            defs.append(_json(read(f"plugins/{plugin}/{ref.removeprefix('./')}")))
    for d in defs:
        for cmd in hook_commands(d):
            out |= {f"plugins/{plugin}/{m}" for m in ROOT_REF.findall(cmd)}
    return out


def decide(changed, read_base, read_head):
    """changed: repo paths changed between base and head. read_*: path -> text or None."""
    reasons = []
    plugins = {}
    for path in changed:
        m = PLUGIN_PATH.match(path)
        if m:
            plugins.setdefault(m.group(1), set()).add(m.group(2))
    for plugin, rels in sorted(plugins.items()):
        for rel in sorted(rels):
            top = rel.split("/", 1)[0]
            if top in ("hooks", "bin"):
                reasons.append(f"plugins/{plugin}/{rel}: under {top}/")
            elif rel == "settings.json":
                reasons.append(f"plugins/{plugin}/settings.json: plugin settings")
        if ".claude-plugin/plugin.json" in rels:
            path = f"plugins/{plugin}/.claude-plugin/plugin.json"
            try:
                old, new = _json(read_base(path)) or {}, _json(read_head(path)) or {}
                for key in RUNTIME_KEYS:
                    if (old.get(key) if isinstance(old, dict) else None) != \
                            (new.get(key) if isinstance(new, dict) else None):
                        reasons.append(f"{path}: {key} changed")
            except Unreadable:
                reasons.append(f"{path}: does not parse")
        try:
            targets = hook_targets(plugin, read_base) | hook_targets(plugin, read_head)
        except Unreadable:
            reasons.append(f"plugins/{plugin}: a hook definition does not parse")
            continue
        for path in sorted(targets & {f"plugins/{plugin}/{r}" for r in rels}):
            if not any(path in r for r in reasons):
                reasons.append(f"{path}: a hook command runs it")
    return {"runtime": bool(reasons), "reasons": reasons}


def git_reader(repo, sha):
    def read(path):
        r = subprocess.run(["git", "-C", repo, "show", f"{sha}:{path}"], capture_output=True)
        return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else None
    return read


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", default=".")
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--github-output")
    a = ap.parse_args(argv)
    if not all(re.fullmatch(r"[0-9a-f]{40}", s) for s in (a.base, a.head)):
        print("runtime_detect: --base and --head must be commit SHAs", file=sys.stderr)
        return 2
    r = subprocess.run(["git", "-C", a.repo, "diff", "--name-only", "--no-renames", "-z", f"{a.base}...{a.head}"],
                       capture_output=True)
    if r.returncode != 0:
        print("runtime_detect: git diff failed", file=sys.stderr)
        return 2
    changed = [p for p in r.stdout.decode("utf-8", "replace").split("\0") if p]
    out = decide(changed, git_reader(a.repo, a.base), git_reader(a.repo, a.head))
    print(json.dumps(out))
    if a.github_output:
        with open(a.github_output, "a", encoding="utf-8") as f:
            f.write(f"runtime={'true' if out['runtime'] else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
