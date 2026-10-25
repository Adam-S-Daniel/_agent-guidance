# Codex memory-hook research for issue 129

Recorded 2026-09-21 against the installed `codex-cli 0.154.0` and the public
[`rust-v0.154.0` source](https://github.com/openai/codex/tree/rust-v0.154.0).
This is evidence for [ADR 0015](../decisions/0015-audit-codex-memories-after-generation-not-at-stop.md).
The September investigation below predates the operator-run auditor; the
October implementation evidence follows it.

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

## 2026-10-02: implementation re-pin to 0.160.0

The worktree started clean on `codex/memory-home-audit-129` at
`90107512f2232f176254daa6dd083015074a08ea`; `git log origin/main..HEAD` was
empty. The installed binary reported `codex-cli 0.160.0` with a temporary
`CODEX_HOME` and `CLAUDE_CONFIG_DIR`. No production memory, settings, or
credentials were inspected. The issue's supplied summary was used offline;
there was no GitHub API request.

Both supplied sparse source trees were inspected read-only:
[`rust-v0.154.0`](https://github.com/openai/codex/tree/rust-v0.154.0)
at `6b9826e3aa83b1a5947db50f4332cb9c65f1b340`, and
[`rust-v0.160.0`](https://github.com/openai/codex/tree/rust-v0.160.0)
at `a956835d020762cb2b570053af06f643a11c0ecc`. Each listed path was compared
with `git diff --no-index -- <154-path> <160-path>`; exit 1 means a difference,
not a failed comparison. Source links in this section pin the new baseline.

| Compared surface | Delta and effect on the auditor |
| --- | --- |
| `state/memory_migrations` | Migration 0001 is unchanged. New [0002](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/state/memory_migrations/0002_consolidation_progress.sql#L1-L6) adds `consolidation_progress(singleton, max_thread_count)` and its initial singleton row. Both migrations define the accepted schema, rather than accepting any file named `memories_1.sqlite`. |
| `state/src/runtime/memories.rs` | Source-selection queries add [creator user/account fields](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/state/src/runtime/memories.rs#L177-L185); the Phase 1 record identity remains `thread_id` plus `source_updated_at`. Successful Phase 2 [updates the maximum selected count](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/state/src/runtime/memories.rs#L1305-L1311), and [reset clears it](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/state/src/runtime/memories.rs#L1434-L1438). This readiness aggregate is not a per-fact completion ledger. Selection still [excludes the current thread and has no cwd/project filter](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/state/src/runtime/memories.rs#L224-L244). |
| `memories/write/src/storage.rs` | Byte-identical. Existing [raw-memory serialization](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/memories/write/src/storage.rs#L44-L77) and rollout filenames remain; namespace and content changes come from the callers. |
| `memories/write/src/phase1.rs` | The writer now selects version-specific input serialization and prompts, and moves parsing to `phase1_output.rs`. V2 [omits raw memory and caps the redacted summary at 9,000 bytes](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/memories/write/src/phase1_output.rs#L33-L56). It [stores empty `raw_memory` with the summary and slug](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/memories/write/src/phase1.rs#L243-L251). The auditor must review a summary-only row, not skip it as empty. V2 also receives [git-branch context and tiered rollout input](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/memories/write/src/phase1.rs#L265-L310); changed model output changes the digest. |
| `memories/write/src/phase2.rs` | Roots and stores are version-specific. [Both versions materialize rollouts, but only v1 rebuilds `raw_memories.md`](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/memories/write/src/phase2.rs#L194-L207). V2's [consolidation template](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/memories/write/templates/memories/consolidation_v2.md#L23-L38) organizes `memory_summary.md` into profile/preferences/tips and project/date retrieval pointers. Rollout files are an audit surface in their own right, particularly in v2. |
| `memories/write/src/workspace.rs` | V2 [does not require `MEMORY.md`](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/memories/write/src/workspace.rs#L86-L112). Its [summary still starts with `v1`, is under 10,000 bytes, and requires four headings](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/memories/write/src/workspace.rs#L117-L129). That first line is not a namespace detector. New [storage-size accounting](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/memories/write/src/workspace.rs#L51-L68) excludes `.git` and symlinks; it adds no home metadata. |
| `memories/write/src/start.rs` | [Dual write spawns a pipeline for each version, each using its own directory](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/memories/write/src/start.rs#L40-L73). Work remains asynchronously spawned; a root Stop is still not the write-completion boundary. |
| `ext/memories` | Reads, injected context, and dedicated tools now use the selected namespace. [V2 injection splits rendered instructions into bounded fragments](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/ext/memories/src/extension.rs#L70-L101); [tools use the same version-specific root](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/ext/memories/src/extension.rs#L150-L158). The relative [ad hoc note path and create-new byte write](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/ext/memories/src/local/ad_hoc_note.rs#L12-L37) are unchanged: `extensions/ad_hoc/notes/` beneath either root. The auditor scans both, irrespective of the active retrieval version. |
| `hooks/src/events/stop.rs` | Byte-identical. Consolidation [still excludes ordinary user/project/session/plugin hooks](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/hooks/src/events/stop.rs#L68-L89). No new completion trigger is justified. |
| `config/src/types.rs` | New [version and dual-write configuration](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/config/src/types.rs#L298-L302) defaults to [v1 and false](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/config/src/types.rs#L352-L360). Previous [numeric defaults](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/config/src/types.rs#L54-L59), generation/use defaults, and dedicated-tools default are unchanged. The stable [`memories` feature remains off](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/features/src/lib.rs#L1158-L1163). |

Following the new namespace calls also established the second database:
[`state/src/sqlite.rs`](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/state/src/sqlite.rs#L81-L95)
names `memories_1.sqlite` and `memories_v2_1.sqlite`, using the same migrations.
The [v2 store is opened on demand](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/state/src/runtime/memory_versions.rs#L10-L25).
Configured [`sqlite_home` or `CODEX_SQLITE_HOME`](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/core/src/config/mod.rs#L4081-L4093)
can relocate those databases; the auditor refuses a detected relocation
instead of silently auditing only files at the wrong root. The supplied
sparse archive did not contain `protocol/src/memory_version.rs`; its missing
blob could not be retrieved offline. Namespace names are independently
established by the [available writer tests](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/memories/write/src/startup_dual_write_tests.rs#L26-L32)
and real database filename code,
rather than attributed to an unread source file.

### Synthetic findings, attestation, and invalidation

The [fixture builder](../../test/fixtures/build-codex-memory.py) creates both
pinned migrations and deterministic SQLx metadata. A fresh temporary home
was built with `--seed phase1`. The following operations used
`python3 scripts/audit-codex-memory.py`, with `CODEX_HOME` and
`CLAUDE_CONFIG_DIR` set to temporary directories; only the fixture's raw
memory was mutated with a parameterized SQLite update. The store was removed
after the run. Default auditor output did not print its memory content.

```text
codex_version: codex-cli 0.160.0
roots: <TEMP_CODEX_HOME>/memories, <TEMP_CODEX_HOME>/memories_v2
audit:
  phase1 identity={namespace: memories, thread_id: fixture-thread}, revision=100
  digest=10eaab01be5cd0875c61a214cc8b52362f5780b4e7b2668d3fdcd8703b9c21e9
  id=0fa0ef901b822d3e96177f0636c5bfb2382a82f124a0fa74ddd96a1cc23ed530
  verdict=unattested; records=1, findings=1; exit=1
attest --record <id-above> --home Adam-S-Daniel/_agent-guidance:docs/decisions/0015-audit-codex-memories-after-generation-not-at-stop.md:
  event=attested; exit=0
audit:
  same identity, revision, digest, and id
  verdict=homed; records=1, findings=0; exit=0
UPDATE stage1_outputs SET raw_memory = ?  [synthetic replacement]
audit:
  same identity and revision
  digest=9dee36bd8976950d0667e7aae27a9e81bad721c835941baa97633b3447f7dd87
  id=85afc719ddc30169bff7d33a9aa3118e6414e764a30c76ce37fd03fafc19fd97
  verdict=unattested; records=1, findings=1; exit=1
synthetic lifecycle checks: 4 passed
```

This is a normalized rendering of actual JSON-lines output; temporary paths
are replaced with placeholders. No actual extracted user memory is claimed.
The persistent sidecar changed only at the attestation step.

### Native schema without credentials or a model request

The installed binary ran `codex app-server --listen stdio://` in an environment
rebuilt with temporary `HOME`, `CODEX_HOME`, `CLAUDE_CONFIG_DIR`, and XDG paths,
and no credentials. A Linux seccomp filter rejected all `socket()` calls.
The config used an unauthenticated synthetic provider, ephemeral credential
storage, disabled analytics/update checks, and disabled memory generation/use.
Only an `initialize` request and `initialized` notification were sent over
stdio. Startup itself created the databases; neither `thread/start` nor a
model turn was needed. The app server exited normally with code 0 and was
reaped, with kill/wait cleanup available on every failure path.

This matches [eager SQLite startup](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/app-server/src/lib.rs#L655-L664)
and [memory migration initialization](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/state/src/runtime.rs#L172-L180).
After exit, inspection used `mode=ro` and `query_only=ON`, without immutable:

```text
app-server exit: 0
created memory database: True
migration versions: [(1,), (2,)]
consolidation_progress: [(1, 0)]
stage1_outputs rows: 0
DDL matches supplied 0.160.0 migrations: True
```

Every memory table and index SQL matched an in-memory database created from
the supplied migrations. The portable fixture's migration strings also matched
those source files byte-for-byte (2 of 2). The shipped auditor then inspected
that native store:

```text
codex_version: codex-cli 0.160.0
roots: <NATIVE_PROBE_ROOT>/codex/memories, <NATIVE_PROBE_ROOT>/codex/memories_v2
audit-codex-memory: no memory store (absent or empty supported surfaces)
records=0, findings=0
exit=0
```

This proves compatibility with the real v1 database migrations, not native
extraction, v2 generation, or per-fact attribution. V2 is covered by the same
pinned migration fixture and namespace-specific audit tests.

### Read-only WAL discovery

A live SQLite writer fixture showed that opening its original database with
`mode=ro`, even with `query_only=ON`, changed original `-shm` bytes as SQLite
coordinated the reader. Its database and WAL content remained unchanged.
The implementation therefore queries a private DB/WAL copy and compares the
original DB/WAL/SHM bytes and identity/stat metadata again afterward. The
regression lane keeps a committed row in the live WAL, verifies it is visible,
and requires all three original MD5 digests to remain unchanged. The auditor
never uses `immutable=1`, which would risk missing that committed WAL content.
Capture checks reject observed movement; they do not establish an atomic
snapshot across all memory surfaces.

### Verifier results and fail-first proof

The independent integration group in [`test/run-tests.sh`](../../test/run-tests.sh)
ran against temporary fixture homes, with a deterministic fake version command:

```text
TEST_GROUP=codex_memory_audit ./test/run-tests.sh
  Results: 85 passed, 0 failed
exit=0
```

For the negative control, only the auditor, test runner, and fixture builder
were copied into a Git-free temporary tree. No `.git`, inherited remote, Git
config, or credentials were copied; `git rev-parse --show-toplevel` refused
the scratch location, and no push was attempted. The mutation removed `digest`
from `record_id`'s canonical tuple. The same group then reported:

```text
FAIL: changed content invalidates attestation — expected exit 1, got 0
FAIL: changed Phase 1 rollout summary invalidates attestation — expected exit 1, got 0
FAIL: mutating one heading section produces a finding — expected exit 1, got 0
FAIL: only changed section loses attestation — got 0
FAIL: sibling attestations remain valid — got 4
FAIL: changed binary skill resource bytes invalidate its attestation — expected exit 1, got 0
  Results: 79 passed, 6 failed
exit=1
```

Restoring the original auditor in that scratch tree returned
`Results: 85 passed, 0 failed`, exit 0. Worktree input MD5 digests matched
before and after both runs; the scratch tree was removed. This proves that
the verifier rejects lost digest invalidation, not merely that an unchanged
implementation happens to return zero.

The whole existing suite was run without a pipe, with isolated outer Codex,
Claude, and HOME directories; the child command was exactly
`./test/run-tests.sh; echo "exit=$?"`:

```text
  Results: 1637 passed, 3 failed
exit=1
```

Only `self_hosted` failed (236 passed, 3 failed); the auditor group within that
run was 85/0, exit 0, and all other shell groups passed. The separate Codex
Cloud unittest group ran 43 tests successfully, though its shell assertion
counter reports 0/0. The three failures are existing Node runner lanes under
Node v20.20.2 in the sandbox:

| Existing lane | Observed runner result | Required count |
| --- | --- | --- |
| `dependabot-config-health` | One file subtest; exit 0, pass=1, fail=0 | At least 30 individual tests |
| `discrepancy-alert` | One file subtest; exit 1, pass=0, fail=1, `ERR_TEST_FAILURE` | At least 20 individual tests |
| `routine-merge-gate` | One file subtest; exit 1, pass=0, fail=1, `ERR_TEST_FAILURE` | At least 58 individual tests |

Direct reruns of those three `node --test --test-reporter=tap` commands
reproduced the one-file reports without exposing individual assertions.
The complete suite therefore remains unverified outside the sandbox; no
claim of a green full suite is made. The orchestrator must rerun it there.

`shellcheck test/run-tests.sh` exited 1 with 142 diagnostics. Running the same
ShellCheck against `git show HEAD:test/run-tests.sh` and comparing diagnostic
code/level/message multisets found the same 142 baseline diagnostics and
**zero introduced diagnostics**. `git diff --check` passed. No workflow,
hook registration, real-memory inspection, push, or PR creation was performed.

## 2026-10-04: `/import` memory destination (source evidence; CLI 0.160.0)

For [issue 183](https://github.com/Adam-S-Daniel/_agent-guidance/issues/183):
on 2026-10-04 `codex --version` reported `codex-cli 0.160.0` (exit 0,
with a read-only PATH-alias warning) and `git ls-remote` dereferenced
`rust-v0.160.0` to `a956835d020762cb2b570053af06f643a11c0ecc` (exit 0).
The exact command/output and source-fetch method are in the
[observation ledger](codex-trust-and-daemon-0160.md#observation-boundary-2026-10-04-codex-cli-01600).
All findings below are source reads on **2026-10-04**, against that tag;
none is a live `/import` result. The importer and consolidation have not
been run, and no real Claude or Codex memory store has been inspected.

- **Selected memory import:** the
  [migration service](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/external-agent-migration/src/service.rs#L394-L434)
  calls `memory_import::import` for a selected, supported memory item.
  [The importer](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/external-agent-migration/src/memory_import.rs#L53-L89)
  requires a nonempty selection and a state database before copying resources.
- **Source and scope:** [discovery](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/external-agent-migration/src/memory.rs#L35-L155)
  recursively selects Markdown files under
  `<external-agent-home>/projects/<project-key>/memory/`, skipping symlinks
  and non-Markdown files. The Claude source's
  [config-directory constant](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/external-agent-migration/src/source/cla.rs#L18-L22)
  is `.claude`. Scope lookup tries session transcripts in newest-first order,
  accepting a recoverable absolute `cwd` that canonicalizes to an existing
  directory; it can fall back to an older usable transcript. A selected
  project without reliable scope is rejected, or an existing unscoped target
  [is removed](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/external-agent-migration/src/memory_import.rs#L130-L145).
- **Destination:** [path construction and copying](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/external-agent-migration/src/memory_import.rs#L281-L372)
  place successful copies at
  `$CODEX_HOME/memories/extensions/external_agent_import/resources/<project-key>/<relative-path>`.
  Relative paths are preserved. Each project receives `scope.json` containing
  its `cwd`; the extension also receives
  [`instructions.md`](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/external-agent-migration/src/memory_import.rs#L161-L167).
  This import path targets `memories/`, not `memories_v2/`.
- **Frontmatter:** the copy path uses `fs::read` followed by `fs::write`
  without parsing or rewriting note bytes. **Inference:** `metadata.home`
  survives in a successfully copied file if it existed in the source. This
  neither adds missing frontmatter nor validates a home. Selected projects
  are replaced wholesale, and selecting a no-longer-discovered project
  [removes its imported directory](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/external-agent-migration/src/memory_import.rs#L130-L145).
- **Consolidation is requested, not proven:** a workspace change
  [attempts to enqueue global consolidation](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/external-agent-migration/src/memory_import.rs#L81-L88);
  enqueue failure is logged without failing the import. The extension's
  [interpretation rules](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/external-agent-migration/src/memory_import.rs#L14-L33)
  instruct consolidation to preserve resource frontmatter, keep detailed
  notes in resources, and route scoped knowledge through `MEMORY.md` and
  `memory_summary.md`. These are instructions, not a guarantee that a later
  consolidation runs, succeeds, or includes every imported fact.

**Audit implication (local file read 2026-10-04; CLI 0.160.0):**
[`file_records`](../../scripts/audit-codex-memory.py) (lines 406–424) scans the three
consolidated root files, Markdown rollout summaries, skills, and ad hoc
notes in each namespace. It does not scan
`extensions/external_agent_import/resources/`. [ADR 0015](../decisions/0015-audit-codex-memories-after-generation-not-at-stop.md#decision)
already excludes other extension/plugin persistence surfaces. Derived text
that actually reaches the supported consolidated files is reviewable there;
raw imported notes can remain outside coverage even when no consolidation
succeeds. Not established: that any source note passed a Claude Stop hook,
contained valid frontmatter, or had a committed repo home. Whether to
extend audit coverage is an owner decision, not resolved by this addendum.

**Owner verification, not performed (planned 2026-10-04; baseline CLI 0.160.0):**
use a disposable profile and project with a synthetic Markdown memory note
containing `metadata.home: example/repo:docs/note.md`, plus a synthetic session
transcript naming that existing project directory. Do not copy production
credentials or real memory. Select only that project memory in `/import`,
then compare source and resource bytes and inspect `scope.json`. Record the
actual versions, destination, and whether consolidation was queued/completed
separately. Include a note lacking frontmatter to demonstrate that byte
preservation does not enforce the home contract. This live check remains
open; the source findings alone do not authorize closing issue 183.

### 2026-10-06: current source and documentation recheck

The latest stable release checked was
[`rust-v0.160.1`](https://github.com/openai/codex/releases/tag/rust-v0.160.1),
published 2026-10-05T18:29:37Z. Relevant source files were unchanged from
0.160.0. This was a source/documentation read only: no CLI reproduction,
`codex --version` invocation, importer execution, or real memory inspection.
The [copy path still uses `fs::read` and `fs::write`](https://github.com/openai/codex/blob/rust-v0.160.1/codex-rs/external-agent-migration/src/memory_import.rs#L281-L309),
and [destination construction still targets the imported resources directory](https://github.com/openai/codex/blob/rust-v0.160.1/codex-rs/external-agent-migration/src/memory_import.rs#L362-L372).
The byte-preservation inference above therefore still applies to successfully
copied raw notes; it does not establish later consolidation behavior. The
[current import documentation](https://learn.chatgpt.com/docs/import), read
2026-10-06, maps Claude Code project memories to Memories but gives no
`metadata.home` preservation guarantee.

The [session-availability discrepancy](../reference/agent-codex.DISCREPANCIES.md#2026-10-06--import-session-availability-docs-disagree-with-release-notes)
records conflicting current documentation and 0.157.0 release notes, with
0.160.1 source reads suggesting the release-note path remains implemented.
Remote behavior has not been reproduced. Use a local TUI for the planned
disposable-profile experiment, the common ground across those documents,
until the discrepancy is settled. The experiment remains open in
[issue 183](https://github.com/Adam-S-Daniel/_agent-guidance/issues/183);
auditor coverage and the existing owner decision above are unchanged.

## 2026-10-08 UTC / 2026-10-07 EDT: live fabricated memory import (CLI 0.161.0)

For [issue 183](https://github.com/Adam-S-Daniel/_agent-guidance/issues/183),
the installed binary reported `codex-cli 0.161.0`, and its
`codex app-server --listen stdio://` process performed the import. This is
runtime evidence from the external-agent migration RPC backend, which also
serves the import flow; it is not a `/import` slash-command UI test or a
remote-session availability test. The documentation disagreement remains
separate in [issue 278](https://github.com/Adam-S-Daniel/_agent-guidance/issues/278).
This extends the source-only observations above and the memory-home design
established for [issue 129](https://github.com/Adam-S-Daniel/_agent-guidance/issues/129)
in [ADR 0015](../decisions/0015-audit-codex-memories-after-generation-not-at-stop.md#addendum--live-import-and-the-memory-home-boundary-2026-10-08-utc--2026-10-07-edt).

### Isolation and reproducible inputs

The probe used fabricated files in disposable `HOME`, `CODEX_HOME`,
`CLAUDE_CONFIG_DIR`, and XDG directories. Its outer environment and the
app-server child environment were rebuilt without inherited credentials.
No real memory, credential, or authentication file was copied or supplied;
no model turn was requested. Every harness run used a PID namespace and an
isolated network namespace. A `claude` sentinel came first on `PATH`, would
record an invocation and exit 97, and recorded **zero calls**. The app server
and all harness children were reaped. Codex initialized its own memory Git
workspace; that workspace had **zero remotes**.

The invocation shape was:

```bash
unshare --user --map-current-user --pid --fork --mount-proc --net -- \
  env -i HOME=<DISPOSABLE_HOME> \
  PATH=<SENTINEL_BIN>:/usr/bin:/bin \
  CLAUDE_SENTINEL_LOG=<SENTINEL_LOG> \
  /usr/bin/python3 <IMPORT_PROBE>
```

The probe started the absolute installed Codex binary with the same sentinel
`PATH`, disposable homes, XDG paths, `TERM=dumb`, and a
`GIT_CEILING_DIRECTORIES` boundary above the fabricated profile. The config
was:

```toml
model = "fixture-model"
model_provider = "fixture"
cli_auth_credentials_store = "ephemeral"
check_for_update_on_startup = false
[analytics]
enabled = false
[feedback]
enabled = false
[features]
external_agent_memory_import = true
[memories]
generate_memories = false
use_memories = false
[model_providers.fixture]
name = "fabricated provider"
base_url = "http://127.0.0.1:9/v1"
wire_api = "responses"
requires_openai_auth = false
supports_websockets = false
```

The feature opt-in matters. A separate fresh-profile control omitted the
`[features]` table, retained the other config and fabricated inputs, and ran
the same detection RPC. It detected **zero memory items** and **one session
item containing one synthetic session**, with the session's cwd matching the
existing fabricated project. The app server exited 0; the control passed
**7 assertions**, exit 0, with zero sentinel calls. This agrees with the
[0.161.0 feature default](https://github.com/openai/codex/blob/rust-v0.161.0/codex-rs/features/src/lib.rs#L1193-L1198)
and the [RPC feature gate](https://github.com/openai/codex/blob/rust-v0.161.0/codex-rs/app-server/src/external_agent_migration/processor.rs#L358-L374).

The fabricated source root was
`$HOME/.claude/projects/fabricated-project/memory/`. Its `note.md` contained
exactly the following UTF-8 bytes, with LF line endings and a final newline:

```markdown
---
name: fabricated import note
description: Synthetic note for byte comparison.
type: project
metadata:
  home: example/repo:docs/note.md
---

Use the example.com fixture endpoint.
```

The second resource, `nested/no-frontmatter.md`, also used LF line endings
and a final newline:

```markdown
# Fabricated note without frontmatter

Use the example.net fixture endpoint.
```

The project directory containing `memory/` also held `synthetic.jsonl` with
one line: `{"type":"user","cwd":"<FABRICATED_EXISTING_PROJECT>","timestamp":"2026-10-07T12:00:00Z","message":{"content":"Fabricated importer receipt."}}`.
The cwd placeholder was replaced with the canonical absolute path of an
existing disposable directory. No session was imported; the transcript
provided scope for the selected memory project.

### RPC sequence and copied bytes

The probe sent `initialize` with
`{"clientInfo":{"name":"fabricated-import-receipt","version":"1.0.0"},"capabilities":{"experimentalApi":true}}`,
then an `initialized` notification. It called `externalAgentConfig/detect`
with `{"includeHome":true,"cwds":[],"migrationSource":"claude"}`. It selected
only the returned `MEMORY` item whose `details.memory` was
`["fabricated-project"]`, then called `externalAgentConfig/import` with
`{"migrationSource":"claude","migrationItems":[<DETECTED_MEMORY_ITEM>]}`.
The empty cwd list avoided project-ancestor configuration discovery. The
probe awaited the matching `externalAgentConfig/import/completed`
notification before closing stdin and reaping the app server, which exited 0.

The sanitized completion receipt was:

```json
{
  "importId": "<FABRICATED_IMPORT_ID>",
  "itemTypeResults": [{
    "itemType": "MEMORY",
    "successes": [{
      "itemType": "MEMORY",
      "cwd": null,
      "source": "fabricated-project",
      "target": "$CODEX_HOME/memories/extensions/external_agent_import/resources",
      "title": null
    }],
    "failures": []
  }]
}
```

The notification reports one synchronized project, not a note count. Both
notes existed at
`$CODEX_HOME/memories/extensions/external_agent_import/resources/fabricated-project/`,
with their relative paths preserved. Source and destination bytes were equal:

| Relative resource path | Bytes | Source and destination SHA-256 |
| --- | ---: | --- |
| `note.md` | 183 | `97f9ffc834f4e70eec2935d5e203fae8e0d76a2a5b8b338189fe5d99152db4bc` |
| `nested/no-frontmatter.md` | 77 | `0a7bd4480b4d8cf586c74ecb9f7b5f30a3ec390cadb643061046b15dde050ef0` |

The literal `metadata.home` value survived in the imported `note.md`.
The second note gained no frontmatter. This establishes byte preservation
for these successful raw-resource copies, not home validation or committed
existence. Both source files remained unchanged. The project's `scope.json`
contained `{"cwd":"<FABRICATED_EXISTING_PROJECT>"}`, matching the canonical
existing project directory. The extension's `instructions.md` existed;
there was no corresponding imported extension under `memories_v2/`.

### Pending consolidation and the observed audit boundary

After the app server exited, read-only inspection of `memories_1.sqlite`
with `mode=ro` and `PRAGMA query_only=ON` found one job:

```json
{
  "kind": "memory_consolidate_global",
  "job_key": "global",
  "status": "pending",
  "finished_at": null,
  "last_success_watermark": 0
}
```

`stage1_outputs` had **zero rows**; `MEMORY.md` and `memory_summary.md` were
absent. Generation and use were disabled deliberately, and no model turn
was started. The completed import notification establishes the resource
operation; the pending job establishes an enqueued consolidation request.
**Consolidation did not complete in this probe.** Nothing here demonstrates
that later consolidation preserves `metadata.home` or includes every
imported fact.

The shipped [auditor](../../scripts/audit-codex-memory.py), invoked as
`python3 scripts/audit-codex-memory.py audit` against this disposable profile
inside the same namespaces, exited **0** and reported:

```text
audit-codex-memory: no memory store (absent or empty supported surfaces)
{"event":"result","findings":0,"records":0}
```

The two imported notes still existed, including the one with no home. A clean
audit therefore describes the supported generated/ad hoc surfaces; it does
not validate raw imported resources. This runtime result confirms the
coverage gap previously identified by reading `file_records` in the linked
auditor. Whether to extend that scope remains an owner decision recorded in
the [ADR addendum](../decisions/0015-audit-codex-memories-after-generation-not-at-stop.md#addendum--live-import-and-the-memory-home-boundary-2026-10-08-utc--2026-10-07-edt).

### Verifier and mutation receipt

The credential-free import harness passed **28 assertions**, exit **0**.
Its checks covered CLI version, normal app-server exit, zero sentinel calls,
selected project and completion identity, one project success with no
failures, both copies and unchanged sources, preserved home, absent added
frontmatter, scope, namespace, extension instructions, queue state, absent
generated output, zero workspace remotes, and the auditor's zero-record
result.

For the negative control, the harness changed only the imported `note.md`
resource's home from `example/repo:docs/note.md` to
`example/repo:docs/changed.md`, leaving the source unchanged. The same byte
comparison failed at **assertion 10**, `imported bytes changed: note.md`,
exit **1**. A fresh restored import passed **28 assertions**, exit **0**.
The separate default-disabled detection control passed **7 assertions**,
exit **0**. Every run used the namespace and sentinel boundary above, and
all children were reaped; no consolidation or real memory was evaluated.
