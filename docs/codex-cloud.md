# Codex Cloud fleet guidance

The manual environment bootstrap is verified for a fresh Codex Cloud
container. It does not depend on user-hook registration or trust; Cloud runs
the installer directly before assembling the agent's instructions.

## Configure the environment

1. Select **Manual** environment setup. Merely adding setup and maintenance
   text while automatic setup remains enabled does not run those scripts, and
   an existing automatic-setup cache can keep using its old setup.
2. Set `CODEX_HOME=/opt/codex` as a persistent environment variable. An
   `export` inside setup does not persist into the agent process.
3. Preserve the repository's dependency installation, then run the hook in
   both the **setup script** and the **maintenance script**:

```bash
npm ci
bash .claude/hooks/fleet-memory.sh --codex-cloud
```

4. Reset the environment cache before the first verification. Cloud runs setup
   while caching the default branch for a fresh container, then checks out the
   task's selected branch. It runs maintenance after checkout when resuming a
   cached container.

Once the hook is on the default branch, the relative command above is the
durable configuration. The successful pre-merge check instead downloaded the
installer from reviewed commit
[`f417c13`](https://github.com/Adam-S-Daniel/_agent-guidance/commit/f417c13d27f99336169966c9709c1a8293686f74),
verified SHA-256
`d33d8e08de029bb118eb1f395b2b4b2f0f4c70b9cb85828ceb2e9580617e1845`,
and pointed `FLEET_GUIDANCE_PAYLOAD` at the current checkout's
`.claude/hooks/fleet-guidance.md`. That pinned the reviewed installer while
still testing the payload from the branch Cloud actually checked out.

The explicit mode targets Codex only. It selects a nonempty
`$CODEX_HOME/AGENTS.override.md` when one exists, otherwise
`$CODEX_HOME/AGENTS.md`, and preserves content outside the fleet's markers. It
also persists one `fleet-guidance:` verdict inside the managed block because
setup stdout is not agent context. Missing payloads, malformed existing
markers, and unwritable destinations print one `DEGRADED` line and exit
nonzero, allowing setup or maintenance to stop instead of silently launching
without the full guidance.

### Mode selection

Only the explicit `--codex-cloud` argument selects Cloud mode; setup and
maintenance supply it directly. `CODEX_HOME` chooses the destination location
only. The hook does not infer Cloud from the operating system, hostname, or
path, so setting `CODEX_HOME` during a normal SessionStart run retains the
legacy behavior: it writes Claude plus that Codex home when the Codex home
already exists, and retains its exit-zero failure policy.

`FLEET_GUIDANCE_SKIP=1` removes the payload and persists an explicit skipped
verdict. Removing the flag (or setting it to `0`, `false`, `no`, or `off`) on a
later setup or maintenance run restores the payload.

## Diagnose delivery without a Codex CLI

Some Cloud shells do not provide a `codex` executable. A short read-only
diagnostic prompt is:

> Do not use tools. Reply with exactly one word: the final word of the
> `fleet-guidance: installed` line in your initial instructions.

The expected word is `maintenance`. This is a convenient session check. The
saved raw task response below is the stronger proof because it does not depend
on a model report.

To prove the installer wrote the file, select the same global file it does and
inspect its persisted verdict:

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
model-visible proof uses
`current_assistant_turn.thread_events.events` in the completed task response.
The checker reads only the initial `rawResponseItem/completed` user
instructions before any tool output, reasoning, or assistant response can echo
the text. In a signed-in browser's developer tools, open the **Network** tab,
reload the completed task, and save the JSON response from
`GET /backend-api/wham/tasks/<task_id>` locally. Then run:

```bash
python3 scripts/check-codex-cloud-context.py \
  saved-task-response.json \
  .claude/hooks/fleet-guidance.md \
  AGENTS.md
```

The checker fails closed unless that envelope contains one byte-exact payload
inside one complete managed block, its one persisted installed verdict, and
the expected repo-specific additions through end of file. It prints only a
non-identifying result; it does not fetch authenticated task data. Raw task
captures are authenticated and stay local—do not commit them or paste private
environment IDs or task URLs into the repo. This response shape is an observed
internal endpoint and may change; the checker fails closed if the response is
unavailable or its structure changes.

## Verification record

On 2026-09-15, the original automatic-setup baseline contained complete
repo-specific additions but no global block or full payload. After switching
to Manual setup, persisting `CODEX_HOME`, and resetting the cache, two completed
Cloud sessions at reviewed commit `f417c13` passed
`check-codex-cloud-context.py`: the 24,465 byte fleet payload appeared exactly
once in each raw initial instruction envelope, and the repo-specific additions
were complete. Both logs said `Running setup scripts...` and
`fleet-guidance: installed`. A third task resumed the cached environment, also
passed the exact initial-envelope checker, and logged
`Running maintenance scripts...` followed by
`fleet-guidance: current (ve8a1ff3c, 24465 bytes) — ~/.codex/AGENTS.md`.
Together these runs verify both cold setup delivery and cached maintenance
delivery. The public record is
[PR #131](https://github.com/Adam-S-Daniel/_agent-guidance/pull/131) and
[issue #130](https://github.com/Adam-S-Daniel/_agent-guidance/issues/130).

A local Codex CLI control independently loaded the complete 24,465 byte global
payload plus a 32,760 byte project `AGENTS.md` into one 57,583 byte instruction
envelope. Refresh and byte-idempotence through the maintenance command have
deterministic test coverage as well as the live cached verification above.
Live Cloud user-hook availability remains unverified; this manual lifecycle
route does not depend on it.

See OpenAI's documentation for [Cloud environment setup and maintenance](https://learn.chatgpt.com/docs/environments/cloud-environment)
and the [Codex `AGENTS.md` instruction hierarchy](https://learn.chatgpt.com/docs/agent-configuration/agents-md).
