import json, subprocess, sys
guard, cases = sys.argv[1], json.load(open(sys.argv[2]))
bad = 0
for tool, arg, want in cases:
    inp = {"command": arg} if tool == "Bash" else {"file_path": arg}
    r = subprocess.run(["bash", guard], input=json.dumps({"tool_name": tool, "tool_input": inp}),
                       capture_output=True, text=True)
    ok = r.returncode == want
    bad += not ok
    print("ok  " if ok else "FAIL", r.returncode, tool, repr(arg))
print(f"{len(cases)} cases, failures: {bad}")
sys.exit(1 if bad else 0)
