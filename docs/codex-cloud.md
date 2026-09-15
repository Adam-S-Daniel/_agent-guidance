# Codex Cloud fleet guidance

This is the candidate bootstrap route for Codex Cloud, pending a positive live
context check. After the change reaches the default branch, add this command
to both the environment's **setup script** and **maintenance script**:

```bash
bash .claude/hooks/fleet-memory.sh --codex-cloud
```

For a cold container, Cloud runs setup while caching the default branch and
checks out the task's selected branch afterward. On a cached container it runs
maintenance after checkout. Once this command exists on the default branch,
setup creates `${CODEX_HOME:-$HOME/.codex}`; maintenance refreshes an older
managed block and is byte-idempotent when the payload is already current.
During pre-merge validation, a relative command against the default branch
cannot exercise this branch-only mode; use only a reviewed, commit-pinned copy
of the hook and payload. Setup-shell exports do not persist into the agent, so
set a custom `CODEX_HOME` in the environment configuration rather than
exporting it only inside setup or maintenance.

The explicit mode targets Codex only. It selects a nonempty
`$CODEX_HOME/AGENTS.override.md` when one exists, otherwise
`$CODEX_HOME/AGENTS.md`, and preserves content outside the fleet's markers. It
also persists one `fleet-guidance:` verdict inside the managed block because
setup stdout is not agent context. Missing payloads, malformed existing
markers, and unwritable destinations print one `DEGRADED` line and exit
nonzero, allowing setup or maintenance to stop instead of silently launching
without the full guidance.

`FLEET_GUIDANCE_SKIP=1` removes the payload and persists an explicit skipped
verdict. Removing the flag (or setting it to `0`, `false`, `no`, or `off`) on a
later setup or maintenance run restores the payload.

## Diagnose delivery without a Codex CLI

Some Cloud shells do not provide a `codex` executable. Select the same global
file the installer does and inspect its persisted verdict:

```bash
codex_home_dir="${CODEX_HOME:-$HOME/.codex}"
if [[ -s "$codex_home_dir/AGENTS.override.md" ]]; then
    codex_global_instructions="$codex_home_dir/AGENTS.override.md"
else
    codex_global_instructions="$codex_home_dir/AGENTS.md"
fi
grep -F 'fleet-guidance:' "$codex_global_instructions"
```

That result proves the setup or maintenance command installed the file. It
does not prove the Cloud harness included the file in the model's context. A
model-visible proof uses the completed task response's raw initial instruction
envelope, before any prompt, tool output, reasoning, or model response can echo
the text. Save the operator-supplied Cloud task response and run:

```bash
python3 scripts/check-codex-cloud-context.py \
  saved-task-response.json \
  .claude/hooks/fleet-guidance.md \
  AGENTS.md
```

The checker fails closed unless that envelope contains one byte-exact payload
inside one complete managed block, its one persisted installed verdict, and
the expected repo-specific additions through end of file. It prints only a
non-identifying result; it does not fetch authenticated task data. Live Cloud
positive verification and rollout are still pending. A completed 2026-09-15
baseline contained the repo-specific additions verbatim but no global managed
block or full payload; the public reproduction is
[_agent-guidance issue #130](https://github.com/Adam-S-Daniel/_agent-guidance/issues/130).
Cloud support for user hooks has not been assumed either way.

See OpenAI's documentation for [Cloud environment setup and maintenance](https://learn.chatgpt.com/docs/environments/cloud-environment)
and the [Codex `AGENTS.md` instruction hierarchy](https://learn.chatgpt.com/docs/agent-configuration/agents-md).
