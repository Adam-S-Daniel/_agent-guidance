#!/usr/bin/env bash
#
# codex-session-start.sh — the Codex SessionStart hook's logic, as a file.
#
# This is the body of the `bash -c '...'` command that
# scripts/register-codex-hook.sh registers as the Linux/macOS hook command,
# kept byte-for-byte equivalent in behavior: find the git root's own synced
# .claude/hooks/fleet-memory.sh and exec it; failing that, from a multi-repo
# parent, exec the first immediate child's workspace-capable copy with
# --workspace; otherwise exit 0 silently.
#
# WHY A FILE. Codex runs a hook's command on Windows through %COMSPEC% as
# `cmd.exe /C "<command>"`
# (https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/hooks/src/engine/command_runner.rs,
# `build_command` / `default_shell_program`). cmd.exe does not treat single
# quotes as quoting, so the inline snippet's `2>/dev/null` and `&&` were
# executed by cmd as its own redirection and command separator and the hook
# never ran. The Windows registration (`commandWindows`) therefore runs
# `"<Git Bash>" "<this file>"`, a command line with no cmd metacharacters.
#
# Codex runs hooks with the session cwd as the working directory, possibly a
# subdirectory, hence the git-root lookup.
r="$(git rev-parse --show-toplevel 2>/dev/null)"
h="$r/.claude/hooks/fleet-memory.sh"
[ -n "$r" ] && [ -f "$h" ] && exec bash "$h"
for h in ./*/.claude/hooks/fleet-memory.sh; do
    grep -q -e --workspace "$h" 2>/dev/null && exec bash "$h" --workspace "$PWD"
done
exit 0
