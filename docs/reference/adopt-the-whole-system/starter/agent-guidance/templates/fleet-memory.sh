#!/usr/bin/env bash
# fleet-memory.sh: SessionStart hook. Copies this repo's guidance payload into
# Claude Code user memory as one marked block, so the guidance loads once per
# session however many repos are attached. Never fails the session.
set -u
say() { echo "fleet-guidance: $*"; exit 0; }
begin='<!-- BEGIN FLEET GUIDANCE (managed) — DO NOT EDIT -->'
end='<!-- END FLEET GUIDANCE -->'
dest_dir="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
dest="$dest_dir/CLAUDE.md"
# Follow a symlinked CLAUDE.md so the link survives the replace below.
if [ -L "$dest" ]; then
  dest=$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$dest" 2>/dev/null) \
    || say "DEGRADED — cannot resolve the symlink at $dest_dir/CLAUDE.md"
fi
# strip_block <file>: print the file without the managed block.
strip_block() { awk -v b="$begin" -v e="$end" '$0==b{skip=1; next} skip&&$0==e{skip=0; next} !skip' "$1"; }
replace() {  # replace <producer command...>: atomically rewrite $dest from its output
  local tmp
  tmp=$(mktemp "$(dirname "$dest")/.CLAUDE.md.XXXXXX") || return 1
  "$@" > "$tmp" && mv "$tmp" "$dest" || { rm -f "$tmp"; return 1; }
}
mkdir -p "$dest_dir" 2>/dev/null || say "DEGRADED — cannot create $dest_dir"
# Two sessions starting at once must not race: read and replace under a lock.
if command -v flock >/dev/null 2>&1 && exec 9>>"$dest_dir/.fleet-guidance.lock"; then
  flock -w 20 9 || say "DEGRADED — another session held the lock for 20 seconds"
fi
if [ -n "${FLEET_GUIDANCE_SKIP:-}" ]; then
  # Opting out also removes a block an earlier session installed.
  if [ -f "$dest" ] && grep -qxF -- "$begin" "$dest"; then
    replace strip_block "$dest" || say "skipped (FLEET_GUIDANCE_SKIP set), but could not remove the old block"
  fi
  say "skipped (FLEET_GUIDANCE_SKIP set)"
fi
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd) || say "DEGRADED — cannot find the hook directory"
payload="$here/fleet-guidance.md"
[ -s "$payload" ] || say "DEGRADED — payload $payload is missing"
if grep -q $'\r' "$payload"; then say "DEGRADED — payload has CRLF line endings; add '.claude/hooks/* text eol=lf' to .gitattributes and re-checkout"; fi
if grep -qxF -e "$begin" -e "$end" "$payload"; then say "DEGRADED — payload contains a block marker line"; fi
# The payload's first line is "<!-- guidance-version: <integer> -->"; the sync
# writes it. An older checkout must never replace a newer block.
new=$(sed -n '1s/^<!-- guidance-version: \([0-9][0-9]*\) -->$/\1/p' "$payload")
[ -n "$new" ] || say "DEGRADED — payload has no guidance-version line"
[ -f "$dest" ] || : > "$dest" 2>/dev/null || say "DEGRADED — cannot write $dest"
old=$(awk -v b="$begin" '$0==b{getline; if (match($0,/guidance-version: [0-9]+/)) print substr($0,RSTART+18,RLENGTH-18); exit}' "$dest")
if [ -n "$old" ] && [ "$old" -gt "$new" ]; then say "current (kept newer v$old; this checkout has v$new)"; fi
if [ "$old" = "$new" ]; then say "current (v$new)"; fi
build() {  # everything outside the markers, then the new block
  strip_block "$dest"
  printf '%s\n' "$begin"
  cat "$payload"
  [ -z "$(tail -c 1 "$payload")" ] || echo   # end the payload with a newline
  printf '%s\n' "$end"
}
replace build || say "DEGRADED — could not replace $dest"
say "installed (v$new, $(wc -c < "$payload" | tr -d ' ') bytes)"
