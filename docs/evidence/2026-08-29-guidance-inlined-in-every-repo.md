# 2026-08-29: the guidance inlined in every repo filled a third of the window

Evidence for the opening of `agents-md/stub.md`,
[Fleet guidance is delivered once per session — not by this file](https://github.com/Adam-S-Daniel/_agent-guidance/blob/main/agents-md/stub.md#fleet-guidance-is-delivered-once-per-session--not-by-this-file).
The rule (check the session-start verdict) stays in the stub; this file keeps
the measurement behind the move to user memory, moved out under
[ADR 0018](../decisions/0018-incident-narratives-live-in-docs-evidence.md).

## Original text

Moved verbatim from the stub's first paragraph (added in `e5b110c`,
"Deliver fleet guidance once per session instead of once per repo"):

> It used to
> be inlined here in every repo, which meant a session with 19 repos open
> carried 19 identical copies: 332.3k tokens of a 1M window, measured
> 2026-08-29.

## Related record

The same measurement is recorded in the header comments of
[`.claude/hooks/fleet-memory.sh`](../../.claude/hooks/fleet-memory.sh)
(37 memory files, 332.3k tokens on a hosted multi-repo session) and
[`scripts/build-agents-md.sh`](../../scripts/build-agents-md.sh).
