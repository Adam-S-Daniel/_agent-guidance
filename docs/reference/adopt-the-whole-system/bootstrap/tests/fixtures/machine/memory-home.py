#!/usr/bin/env python3
"""memory-home.py: Claude Code Stop hook (user level). Blocks the stop while a
memory note written in the last day has no metadata.home, so the agent gives
it one. Notes with metadata.type "user" (about the person) are exempt. It never
edits or deletes a note. Any failure of the hook itself lets the stop happen."""
import glob, json, os, sys, time
try:
    import yaml
except ImportError:
    sys.exit(0)
try:
    event = json.load(sys.stdin)
except Exception:
    sys.exit(0)
if event.get("stop_hook_active"):
    sys.exit(0)  # already blocked once this turn; never loop
# This session's project only: its transcript sits beside the memory directory.
transcript = event.get("transcript_path") or ""
if not transcript:
    sys.exit(0)
missing = []
for note in glob.glob(os.path.join(os.path.dirname(transcript), "memory", "*.md")):
    if os.path.basename(note) == "MEMORY.md" or time.time() - os.path.getmtime(note) > 86400:
        continue
    try:
        text = open(note, encoding="utf-8").read()
        front = yaml.safe_load(text.split("---", 2)[1]) if text.startswith("---") else {}
    except Exception:
        front = {}
    front = front if isinstance(front, dict) else {}
    meta = front.get("metadata") if isinstance(front.get("metadata"), dict) else {}
    kind = meta.get("type") or front.get("type")
    if kind != "user" and not (meta.get("home") or front.get("home")):
        missing.append(note)
if missing:
    print(json.dumps({"decision": "block", "reason":
        "These memory notes have no metadata.home (owner/repo:path of the committed "
        "file that holds the fact). Put the fact in that file, then add the home: "
        + ", ".join(sorted(missing))}))
