# Codex project trust and daemon refresh: source evidence

For [issue 181](https://github.com/Adam-S-Daniel/_agent-guidance/issues/181)
and [issue 182](https://github.com/Adam-S-Daniel/_agent-guidance/issues/182),
supporting the addendum to
[ADR 0012](../decisions/0012-codex-gets-the-guidance-as-user-instructions.md).

## Observation boundary (2026-10-04; codex-cli 0.160.0)

Observed in this run, from the assigned worktree:

```text
$ codex --version
WARNING: proceeding, even though we could not create PATH aliases: Read-only file system (os error 30)
codex-cli 0.160.0

$ git ls-remote https://github.com/openai/codex.git 'refs/tags/rust-v0.160.0' 'refs/tags/rust-v0.160.0^{}'
79b1b666f2e8551f8abbbca34957227f67f3f553 refs/tags/rust-v0.160.0
a956835d020762cb2b570053af06f643a11c0ecc refs/tags/rust-v0.160.0^{}
```

Both commands exited 0. The last line dereferences the annotated tag to the
source commit. Cited files were downloaded from
`https://raw.githubusercontent.com/openai/codex/rust-v0.160.0/<path>` with
Python `urllib.request.urlopen`, then read with `sed` or `nl`. These are
**source observations**, not executions. Upstream tests were read, not run.
The installed CLI version does not establish a running daemon's version.

The inherited draft's scratch-home trust matrix was not rerun. Its measured
claims and probe script were removed because this package forbids changing
Codex configuration to conduct an experiment. Evidence collection did not
invoke `prompt-input`, execute a live Codex hook, start a model turn, inspect
credentials or memory stores, change Codex configuration, or contact/restart
a daemon. The required repository verifier separately exercises synthetic
hook/config fixtures; those tests are not live trust or daemon observations.

## Trust gates (source read 2026-10-04; CLI 0.160.0; rust-v0.160.0)

- **Project instructions:**
  [`load_project_instructions`, lines 55–67](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/core/src/agents_md.rs#L55-L67)
  constructs the user-instruction result first, then returns it without
  project discovery when `active_project.is_untrusted()` is true.
  [`ProjectConfig`, lines 602–615](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/config/src/config_toml.rs#L602-L615)
  defines that predicate as explicit `trust_level = "untrusted"`. Missing
  trust does not trigger this guard. This does not establish whether the
  interactive UI lets an unfamiliar project proceed.
- **Project configuration:**
  [`disabled_reason_for_decision`, lines 1087–1107](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/config/src/loader/mod.rs#L1087-L1107)
  gates project-local config, hooks, and exec policies unless trusted. Missing
  trust and explicit untrusted status therefore differ for instruction
  discovery, but both disable this project config layer. The
  [official configuration documentation](https://learn.chatgpt.com/docs/config-file/config-basic#configuration-precedence),
  read on this date, also says untrusted project `.codex/` layers are skipped
  while user and system layers still load.
- **Hook definition trust is another gate:** discovery iterates
  [enabled config layers](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/hooks/src/engine/discovery.rs#L129-L186),
  whose [iterator excludes disabled layers](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/config/src/state.rs#L544-L552).
  A handler also needs to be enabled and satisfy
  [hook trust](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/hooks/src/engine/discovery.rs#L677-L717);
  [the hash comparison](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/hooks/src/engine/discovery.rs#L794-L821)
  determines whether a non-managed definition is trusted. Managed policy can
  [restrict discovery to managed hooks](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/hooks/src/engine/discovery.rs#L108-L145).
  User-layer discovery does not guarantee execution.

**Repository implication, inferred:** the user-level
[registrar](../../scripts/register-codex-hook.sh) selects a checkout's own
`.claude/hooks/fleet-memory.sh` in its command (lines 178–180). Project-layer
gating alone does not remove that user-layer definition. If enabled, trusted,
and allowed by managed policy, it remains eligible for discovery. This is
not a live demonstration that the fleet hook executes in an untrusted
project. The [hash input is normalized configuration](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/hooks/src/engine/discovery.rs#L766-L792),
so trusting the definition does not establish the trustworthiness of the
checkout script's current contents.

## Prompt debugging (source read 2026-10-04; CLI 0.160.0; rust-v0.160.0)

The CLI's
[`run_debug_prompt_input_command`](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/cli/src/main.rs#L1970-L2060)
constructs its own global-instruction provider and calls the in-process
`codex_core::build_prompt_input` with no state database.
[`build_prompt_input_from_session`](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/core/src/prompt_debug.rs#L83-L110)
captures a step without entering `run_turn`; the
[`SessionStart` call](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/core/src/session/turn.rs#L322-L324)
is in `run_turn`.

Consequently, this command cannot by itself demonstrate SessionStart hook
execution or what an already-running daemon served. Absence of the hook's
emitted `fleet-guidance:` verdict is not a delivery-failure signal for this
command. A managed block in its global instruction fragment demonstrates
what this fresh diagnostic process loaded from disk, not that the current
session's hook just installed it. No diagnostic output was collected here.

## Refresh limits (source read 2026-10-04; CLI 0.160.0; rust-v0.160.0)

1. The app-server creates a
   [`CodexHomeUserInstructionsProvider`](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/app-server/src/message_processor.rs#L334-L357)
   for its thread manager; root threads
   [receive a clone](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/core/src/thread_manager.rs#L1800-L1834).
   The provider
   [checks disk on every load](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/codex-home/src/instructions/mod.rs#L40-L109),
   preferring nonempty `AGENTS.override.md` over `AGENTS.md`. When no
   instructions load and warnings exist, it retains the last successful
   value; absence or empty files without warnings clear it.
2. Refresh runs at
   [session initialization](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/core/src/session/session.rs#L1446-L1474)
   and during
   [step-context capture](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/core/src/session/mod.rs#L3841-L3851).
   [`AgentsMdManager::refresh`](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/core/src/agents_md_manager.rs#L98-L140)
   invokes the provider before deciding whether to reuse a snapshot. The
   upstream [same-turn refresh test](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/core/tests/suite/agents_md_refresh.rs#L322-L395)
   expects a replacement fragment after a file change between requests. Its
   [failed-refresh test](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/codex-home/src/instructions/tests.rs#L151-L196)
   expects retention until recovery or removal. Neither test was run here.
3. `run_turn`
   [captures and records its first context](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/core/src/session/turn.rs#L257-L324)
   before pending SessionStart hooks run, then ordinarily
   [reuses it for the first request](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/core/src/session/turn.rs#L424-L473).
   **Inference:** if a hook rewrites global instructions after that capture,
   the first request can use the prior copy (or none on first install), with
   a later capture eligible to read the replacement. This is core turn
   ordering, not proof of daemon-specific caching. No live lag was measured.
4. This does **not** settle general configuration freshness. The
   [config manager](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/app-server/src/config_manager.rs#L42-L56)
   retains process state, including CLI overrides. Its
   [reload methods](https://github.com/openai/codex/blob/rust-v0.160.0/codex-rs/app-server/src/config_manager.rs#L189-L258)
   combine refreshed layers with retained session layers. Global AGENTS
   rereads do not establish immediate refresh of every setting, hook
   definition, provider, or project instruction in every loaded thread.

**Source-only conclusion:** this implementation has a disk-refresh path for
global instructions; the inherited claim that daemon staleness "cannot"
occur was too broad. Override precedence, failed reads, hook ordering, and
retained configuration need separate consideration.

## Owner experiment, not performed (planned 2026-10-04; baseline CLI 0.160.0)

Live verification for
[issue 181](https://github.com/Adam-S-Daniel/_agent-guidance/issues/181) and
[issue 182](https://github.com/Adam-S-Daniel/_agent-guidance/issues/182)
needs setup changes or real sessions outside this package's boundary.

Use a disposable project and an owner-selected profile with harmless markers;
do not copy production credentials. Record client and actual daemon versions,
trust state, override-file selection, and hook hash/enabled state. Compare
trusted and explicitly untrusted sessions for project instructions and user
versus project hook execution. Capture the actual session's initial request or
instruction diagnostic; model recollection alone is not byte-level proof.

Keep the same daemon alive, change only the instruction marker, then capture a
new session and the next step of an existing one. Separately test a SessionStart
rewrite to distinguish first-request ordering from process staleness. Test
config and hook-definition changes separately; compare with a fresh standalone
process, and restart only under owner control. Repeat on native Windows if
needed. `codex debug prompt-input` is a fresh-process disk baseline, not a
daemon capture. This evidence does not authorize closing either issue.
