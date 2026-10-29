#!/usr/bin/env bash
# agent-checks-guard.sh: Claude Code PreToolUse hook (matcher
# "Bash|Edit|Write|MultiEdit"). Refuses the easy ways to skip or switch off the
# commit-time checks, and deleting a persistent/* branch. It stops habit, not
# intent; the required CI check is the enforcement. Exit 2 blocks the tool
# call; any failure of this script itself lets the call run.
command -v python3 >/dev/null 2>&1 || exit 0
exec python3 -I -c '
import json, os, re, shlex, sys
try:
    event = json.load(sys.stdin)
except Exception:
    sys.exit(0)
tool = event.get("tool_name") or ""
inp = event.get("tool_input") or {}

def refuse(why):
    print("agent-checks-guard: refused: " + why + ". Fix what the check reported "
          "instead; if the check is wrong, change the check in its own pull request.",
          file=sys.stderr)
    sys.exit(2)

if tool in ("Edit", "Write", "MultiEdit"):
    path = (inp.get("file_path") or "").replace("\\", "/")
    if "/.githooks/" in "/" + path or "agent-hooks.inc" in path or "/.config/git/" in path \
            or os.path.basename(path) in (".gitconfig", "gitconfig") or path.startswith("/etc/gitconfig") \
            or path.endswith("/.git/config") or path.endswith("/.claude/hooks/agent-checks-guard.sh"):
        refuse("editing the commit-time checks, their guard, or the git config that runs them")
    sys.exit(0)
if tool != "Bash":
    sys.exit(0)
cmd = inp.get("command") or ""
low = cmd.lower()
# The same text with quotes and backslashes removed, so --no-"verify" and
# --n\o-verify read as what the shell will make of them.
flat = re.sub(r"[\x22\x27\x5c]", "", low)

# Lexical checks on the raw text catch forms the word parser below cannot see:
# bash -c "...", $(...), heredocs, and environment-variable config.
if re.search(r"\bgit\b[^\n]*\s--no-v", flat):
    refuse("--no-verify (or an abbreviation of it) skips the agent-checks hooks")
# Changing the config the hooks come from: refused outright.
if re.search(r"\bgit\b[^\n]*\bconfig\b(?![^\n]*--(get|list)\b)[^\n]*\b(core\.hookspath|includeif\.|hook\.)"
             r"|agent-hooks\.inc", flat):
    refuse("changing the git config that runs the checks switches them off")
# Pointing one command at other config (env vars, HOME, -c, --config-env):
# refused only for the commands the hooks run on, commit and push, so test
# scripts may still use a scratch HOME or GIT_CONFIG_GLOBAL.
if re.search(r"\bgit\b[^\n]*\b(commit|push)\b", flat) and re.search(
        r"\bgit_config\w*=|\bgit_config_nosystem\b|--config-env|(^|[\s;&|(])(home|xdg_config_home)=|core\.hookspath", flat):
    refuse("running commit or push with other git config switches the checks off")
if re.search(r"\bgit\b[^\n]*\bpush\b[^\n]*(--delete|\s-d\s|\s\+?:)[^\n]*persistent/", flat) \
        or re.search(r"-x\s*delete[^\n]*git/refs/heads/persistent/", flat):
    refuse("persistent/* branches hold generated data and are never deleted")

try:
    words = shlex.split(cmd)
except ValueError:
    # Unparseable (an unbalanced quote, say): fall back to a plain text test.
    if re.search(r"\bgit\b[^\n]*\bcommit\b[^\n]*\s-[a-z]*n", flat):
        refuse("git commit -n skips the agent-checks hooks")
    sys.exit(0)
GLOBAL_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
seg = []
for w in words + [";"]:
    if w not in ("&&", "||", ";", "|", "&"):
        seg.append(w.rstrip(";"))
        continue
    gits = [i for i, x in enumerate(seg) if os.path.basename(x) == "git"]
    if gits:
        args = seg[gits[0] + 1:]
        i, sub = 0, ""
        while i < len(args):
            a = args[i]
            if a in GLOBAL_WITH_VALUE:
                if a == "-c" and i + 1 < len(args) and args[i + 1].lower().startswith(("hook.", "core.hookspath")):
                    refuse("changing hook configuration switches the checks off")
                i += 2
                continue
            if a.startswith("-"):
                i += 1
                continue
            sub = a
            break
        rest = args[i + 1:]
        if sub == "commit" and any(a.startswith("-") and not a.startswith("--") and "n" in a[1:] for a in rest):
            refuse("git commit -n skips the agent-checks hooks")
        reading = any(a in ("--get", "--get-all", "--get-regexp", "--list", "-l") for a in rest)
        if sub == "config" and not reading and any(a.lower().startswith(("hook.", "core.hookspath", "includeif")) for a in rest):
            refuse("changing hook configuration switches the checks off")
    seg = []
'
