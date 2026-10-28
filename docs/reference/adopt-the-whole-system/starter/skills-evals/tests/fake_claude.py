#!/usr/bin/env python3
"""A fake `claude` for the tests. Behavior comes from fake.json beside this
file; every call appends one JSON line to calls.jsonl beside it.

It emits what `claude -p --output-format json --verbose` prints: a JSON list of
messages including a rate_limit_event and a result. It edits the workspace per
arm: if $CLAUDE_CONFIG_DIR/skills exists it copies the fixture's solution/
overlay (the skill helped); otherwise it only touches README.md.
"""
import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = HERE / "fake.json"
LOG = HERE / "calls.jsonl"


def main() -> int:
    argv = sys.argv[1:]
    if argv[:1] == ["--version"]:
        print("2.1.999 (Claude Code)")
        return 0
    st = json.loads(STATE.read_text())
    cfg = Path(os.environ.get("CLAUDE_CONFIG_DIR", "/nonexistent"))
    prompt = argv[argv.index("-p") + 1] if "-p" in argv else ""
    kind = "probe" if prompt.startswith("Reply with the word ok") else \
        "judge" if "CANDIDATE" in prompt else "trial"
    settings = {}
    if (cfg / "settings.json").exists():
        settings = json.loads((cfg / "settings.json").read_text())
    with LOG.open("a") as f:
        f.write(json.dumps({
            "kind": kind, "args": argv[:1] + argv[2:], "prompt_has_arm_names":
                any(w in prompt for w in ("with_skill", "without_skill", "bare")),
            "env": sorted(os.environ), "cwd": os.getcwd(), "config": str(cfg),
            "has_skill": (cfg / "skills").exists(), "settings": settings,
            "checks_visible": os.path.exists("checks"),
            "git_commits": int(os.popen("git rev-list --count HEAD 2>/dev/null").read().strip() or 0),
            "home_is_config": os.environ.get("HOME") == str(cfg),
        }) + "\n")
    mode = st.get("mode", "normal")
    if mode == "sandbox_fail" and kind == "trial":
        print("Error: sandbox unavailable: bubblewrap not found (failIfUnavailable)", file=sys.stderr)
        return 1
    st["meter"] = round(st["meter"] + st["step"], 6)
    STATE.write_text(json.dumps(st))
    msgs = [{"type": "system", "subtype": "init", "model": "claude-sonnet-5-5"}]
    if kind == "trial":
        if mode == "write_config":
            (cfg / "CLAUDE.md").write_text("tampered")
        if mode == "write_runtime_state":
            (cfg / ".claude.json").write_text("{}")
        if mode == "touch_fixture":
            Path(st["touch"]).write_text("changed during the run")
        if mode != "no_edit":
            if (cfg / "skills").exists() and st.get("solution"):
                shutil.copytree(st["solution"], ".", dirs_exist_ok=True)
            else:
                Path("README.md").write_text("# touched\n")
        msgs.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Edit", "input": {"file_path": "secret-path"}}]}})
        msgs.append({"type": "user", "message": {"content": [
            {"type": "tool_result", "is_error": False, "content": "ok"}]}})
    if mode != "no_meter":
        msgs.append({"type": "rate_limit_event", "rate_limit_info": {"unifiedWindows": {
            "seven_day": {"utilization": st["meter"], "resetsAt": st["resets"]}}}})
    text = "done"
    if kind == "judge":
        text = json.dumps({"scores": {l: {"completeness": 7, "salience": 6, "restraint": 5}
                                      for l in "ABC"}, "notes": "x"})
    if mode == "no_result" and kind == "trial":
        print(json.dumps(msgs))
        return 0
    msgs.append({"type": "result", "subtype": "success",
                 "is_error": mode == "error_result" and kind == "trial",
                 "num_turns": 3, "total_cost_usd": 0.01, "result": text,
                 "usage": {"input_tokens": 100, "output_tokens": 20,
                           "cache_read_input_tokens": 5, "cache_creation_input_tokens": 1},
                 "modelUsage": {"claude-sonnet-5-5": {}}})
    print(json.dumps(msgs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
