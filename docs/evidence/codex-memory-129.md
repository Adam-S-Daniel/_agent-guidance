# Codex memory-hook research for issue 129

Recorded 2026-09-21 against the installed `codex-cli 0.154.0` and the public
[`rust-v0.154.0` source](https://github.com/openai/codex/tree/rust-v0.154.0).
This is evidence for [ADR 0015](../decisions/0015-audit-codex-memories-after-generation-not-at-stop.md),
not a shipped hook or memory adapter.

## Source findings

The lifecycle command inputs and accepted outputs are defined as follows:

| Event | JSON on stdin | Meaningful command output |
| --- | --- | --- |
| `SessionStart` | `session_id`, nullable `transcript_path`, `cwd`, `hook_event_name`, `model`, `permission_mode`, and `source` (`startup`, `resume`, `clear`, or `compact`) ([schema](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/hooks/src/schema.rs#L496-L543), [source values](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/hooks/src/events/session_start.rs#L23-L40)) | Universal fields `continue`, `stopReason`, `suppressOutput`, and `systemMessage`, plus `hookSpecificOutput: {hookEventName: "SessionStart", additionalContext}` ([schema](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/hooks/src/schema.rs#L87-L99), [event output](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/hooks/src/schema.rs#L384-L403)). Plain non-JSON stdout also becomes model context; invalid JSON-looking stdout fails; `continue:false` stops startup ([parser](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/hooks/src/events/session_start.rs#L216-L310)). |
| `SessionEnd` | `session_id`, nullable `transcript_path`, `cwd`, `hook_event_name`, and constant reason `other` ([schema](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/hooks/src/schema.rs#L512-L523), [serialization](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/hooks/src/events/session_end.rs#L50-L90)) | No JSON output contract. Exit zero completes; a nonzero exit fails and uses non-empty stderr as the error text. The default timeout is one second and the maximum is three ([parser and limits](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/hooks/src/events/session_end.rs#L20-L24), [parser](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/hooks/src/events/session_end.rs#L96-L125)). |
| `Stop` | `session_id`, `turn_id`, nullable `transcript_path`, `cwd`, `hook_event_name`, `model`, `permission_mode`, `stop_hook_active`, and nullable `last_assistant_message` ([schema](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/hooks/src/schema.rs#L585-L601), [serialization](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/hooks/src/events/stop.rs#L150-L176)) | Universal fields plus optional `decision:"block"` and required non-empty `reason` for a block ([schema](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/hooks/src/schema.rs#L451-L464), [parser](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/hooks/src/events/stop.rs#L250-L369)). A block reason becomes a continuation prompt; Codex sets `stop_hook_active=true` before sampling again ([turn loop](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/core/src/session/turn.rs#L552-L587)). That boolean is a loop signal to the hook, not an automatic block limit. |
| `PostCompact` | `session_id`, `turn_id`, optional `agent_id` and `agent_type`, nullable `transcript_path`, `cwd`, `hook_event_name`, `model`, and trigger `manual` or `auto` ([schema](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/hooks/src/schema.rs#L364-L382), [serialization](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/hooks/src/events/compact.rs#L205-L217)) | Universal fields only. `continue:false` stops; `stopReason` supplies the stop entry; other valid output has no context-injection field ([schema](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/hooks/src/schema.rs#L177-L184), [parser](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/hooks/src/events/compact.rs#L254-L311)). |

Hook trust attaches to a normalized configuration definition. Codex normalizes
the event, matcher group, and one handler, converts that value to canonical
JSON, and stores its SHA-256 as `sha256:<hex>`
([definition hash](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/hooks/src/engine/discovery.rs#L766-L792),
[canonical hash](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/config/src/fingerprint.rs#L50-L79)).
Changing the contents of a script named by an unchanged command string does
not change this definition hash. Non-managed trust is `Trusted` only when the
saved hash equals the current hash; a different saved hash is `Modified`, and
no saved hash is `Untrusted`
([classification](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/hooks/src/engine/discovery.rs#L794-L815)).
Only user and session-flag layers can write hook enablement and trusted hashes;
project, managed, and plugin layers can discover hooks but cannot set that
user state
([layer rule](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/hooks/src/config_rules.rs#L8-L65)).
The local probe deliberately used `--dangerously-bypass-hook-trust`; it
therefore proves runtime transport, not persisted-trust UI behavior.

The native memory worker is a separate boundary. A memory-consolidation turn
maps to a special `StopHookTarget::MemoryConsolidation`
([runtime selection](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/core/src/hook_runtime.rs#L375-L451)).
For that target, Codex excludes user, project, session-flag, and plugin Stop
hooks, retaining executor cleanup and managed-policy sources
([selection](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/hooks/src/events/stop.rs#L41-L93)).
The upstream regression test expressly asserts that the memory worker does not
run the user Stop hook
([test](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/memories/write/src/startup_tests.rs#L180-L302)).
That prevents a normal user Stop hook from observing the consolidation worker
from inside that worker. It does not prevent a root-session hook or separate
adapter from inspecting memory state that Codex has already persisted.

## Credential-free CLI probe

The probe ran the installed binary against a Python HTTP server bound only to
`127.0.0.1`. The server returned two canned Responses API SSE completions. The
process environment was cleared with `env -i`; `HOME` and `CODEX_HOME` pointed
to fresh directories under `/tmp`; the config set
`cli_auth_credentials_store="ephemeral"`,
`requires_openai_auth=false`, `supports_websockets=false`, and disabled native
memory generation and use. No credential was read, copied, or supplied.

Before the CLI started, the harness manually created this file inside the
temporary Codex home:

```text
memories/MEMORY.md
SYNTHETIC MEMORY FIXTURE: manually created; not native Codex memory output.
```

The `SessionStart` fixture hook read that exact file and returned its text as
`additionalContext`. The Stop fixture returned
`{"decision":"block","reason":"synthetic gate: repeat once"}` only while
`stop_hook_active` was false. `SessionEnd` logged stdin and returned no output.
`PostCompact` was configured but the non-interactive two-response run did not
compact, so it did not fire. No PostCompact observation is claimed.

The effective invocation shape was:

```bash
env -i \
  HOME=<TEMP_HOME> \
  CODEX_HOME=<TEMP_CODEX_HOME> \
  PATH=/usr/local/bin:/usr/bin:/bin:<CODEX_BINARY_DIR> \
  codex exec \
    --dangerously-bypass-hook-trust \
    --skip-git-repo-check \
    --sandbox read-only \
    --json \
    -C <TEMP_WORKSPACE> \
    "Return the synthetic fixture response."
```

The final run used temporary path
`/tmp/codex-129-probe/run.CvK2gH`; the path and all thread/turn identifiers are
normalized below because they have no semantic value. The command exited 0,
the mock received exactly two requests, and the observed hook stdin was:

```json
{"hook_event_name":"SessionStart","model":"fixture-model","permission_mode":"bypassPermissions","source":"startup","transcript_path":"<TRANSCRIPT>","cwd":"<WORKSPACE>"}
{"hook_event_name":"Stop","last_assistant_message":"synthetic assistant response 1","model":"fixture-model","permission_mode":"bypassPermissions","stop_hook_active":false,"transcript_path":"<TRANSCRIPT>","cwd":"<WORKSPACE>"}
{"hook_event_name":"Stop","last_assistant_message":"synthetic assistant response 2","model":"fixture-model","permission_mode":"bypassPermissions","stop_hook_active":true,"transcript_path":"<TRANSCRIPT>","cwd":"<WORKSPACE>"}
{"hook_event_name":"SessionEnd","reason":"other","transcript_path":"<TRANSCRIPT>","cwd":"<WORKSPACE>"}
```

Request 1 contained the manually created fixture text as a developer input.
After the first Stop hook blocked, request 2 contained
`<hook_prompt ...>synthetic gate: repeat once</hook_prompt>`. The second Stop
stdin carried `stop_hook_active:true`, the hook allowed completion, and
`SessionEnd` followed. This demonstrates the lifecycle transport and repeat
guard. It does not demonstrate native memory production or native attribution.

The successful verifier was:

```text
bash /tmp/codex-129-probe/run_probe.sh
PASS: 13 assertions
SQLite files inspected read-only: 6
exit 0
```

The 13 assertions checked all four observed hook payloads, both assistant
messages, the false-to-true Stop guard transition, exactly two provider
requests, SessionStart context in request 1, and Stop feedback in request 2.
The negative control deliberately changed the expected first guard value:

```text
python3 /tmp/codex-129-probe/assert_probe.py <PROBE_ROOT> \
  --expect-first-active true
AssertionError: assertion 5 failed: unexpected first stop_hook_active value
exit 1
```

After `codex exec` had exited, the verifier opened every temporary SQLite database with a
`file:<PATH>?mode=ro` URI and immediately set `PRAGMA query_only=ON`. Six
Codex-created SQLite files existed. The memory-specific one had this schema
and row count:

| Table | Columns | Rows |
| --- | --- | ---: |
| `_sqlx_migrations` | `version`, `description`, `installed_on`, `success`, `checksum`, `execution_time` | 1 |
| `jobs` | `kind`, `job_key`, `status`, `worker_id`, `ownership_token`, `started_at`, `finished_at`, `lease_until`, `retry_at`, `retry_remaining`, `last_error`, `input_watermark`, `last_success_watermark` | 0 |
| `stage1_outputs` | `thread_id`, `source_updated_at`, `raw_memory`, `rollout_summary`, `rollout_slug`, `generated_at`, `usage_count`, `last_usage`, `selected_for_phase2`, `selected_for_phase2_source_updated_at` | 0 |

Those zero memory rows are consistent with the explicit memory-disable config
and the fresh home. The manual `MEMORY.md` fixture is a normal file and is not
evidence of a native job. Because inspection happened after the process ended,
this read does not establish that the same files or rows were visible at any
particular hook phase. A final process check found no remaining fake server,
probe runner, or `codex exec` process.

For repository-wide context, the existing suite was also run outside the
sandbox with temporary Codex, Claude, and Git config homes after
`npm ci --ignore-scripts`: `./test/run-tests.sh` reported
`1391 passed, 0 failed` and exited 0. No repository code existed in this
research change for that baseline to exercise.
