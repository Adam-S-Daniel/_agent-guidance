# 2026-10-04: a test's `killpg` on a mocked pid killed every user process

Evidence for the `agents-md/base.md` section
[A test that can signal can kill every session](https://github.com/Adam-S-Daniel/_agent-guidance/blob/main/agents-md/base.md#a-test-that-can-signal-can-kill-every-session).
The rules stay in that section; this file keeps the story that justifies
them, moved out under [ADR 0018](../decisions/0018-incident-narratives-live-in-docs-evidence.md).

## Original text

Moved verbatim from the section's opening paragraph (added in
`13cb308`, [_agent-guidance#245](https://github.com/Adam-S-Daniel/_agent-guidance/pull/245)):

> 2026-10-04: a skills-evals test ([#250](https://github.com/Adam-S-Daniel/skills-evals/pull/250))
> mocked `Popen`; cleanup's `os.killpg(proc.pid, SIGKILL)` hit the mock.
> `MagicMock` converts to the int `1` and `killpg(1, sig)` is `kill(-1, sig)`:
> every user process died.

## Related record

- The section's [`docs/guidance-impact.md`](../guidance-impact.md) entry
  `2026-10-04 — test-that-can-signal-kills-every-session — create` records
  that the kill recurred about 15 times when resumed agents re-ran the suite.
- The guard that refuses any pid but an `int` > 1 is exercised in this repo
  by [`test/test_codex_cloud_receipt.py`](../../test/test_codex_cloud_receipt.py).
