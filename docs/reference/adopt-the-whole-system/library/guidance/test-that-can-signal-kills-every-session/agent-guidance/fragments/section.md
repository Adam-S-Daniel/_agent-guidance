## A test that can signal can kill every session

A mocked pid became `kill(-1, SIGKILL)`; every user process died
([evidence](https://github.com/example-adopter/_agent-guidance/blob/main/docs/evidence/2026-10-04-killpg-on-a-mocked-pid.md)).

- **Code that signals refuses any pid or group but an `int` > 1**, before the
  call.
- **A test that mocks a spawn patches every signal call** the code can reach.
- **A sandboxed 137 with no OOM is a finding.** Never escalate out
  (`require_escalated`, `dangerouslyDisableSandbox`); it contained the
  kill. Find what sent the signal.
- **A session that dies mid-verifier is evidence against it.** After a
  restart, find what killed it (journal, 137, `dmesg`) before re-running:
  resumed agents re-running the suite made one kill fifteen.
- **Run spawning or signaling suites in a PID namespace:**
  `unshare --user --map-current-user --pid --fork --mount-proc -- <cmd>`.
