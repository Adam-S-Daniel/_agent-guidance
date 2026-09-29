# 0015 — Audit Codex memories after generation, not at Stop

**Status:** Proposed (2026-09-21)

## Context

[ADR 0013](0013-a-memory-note-outside-a-repo-names-its-home.md) makes a Claude
Code memory note either a pointer to a committed repo home or an explicit
`type: user` exemption, and its `Stop` hook gates only notes written by that
session. [_agent-guidance issue 129](https://github.com/Adam-S-Daniel/_agent-guidance/issues/129)
asked whether Codex can carry the same contract. The issue reserved ADR 0014;
[ADR 0014](0014-dependabot-config-health-is-swept-centrally.md) was already on
the branch when this research began, so this record is 0015. Nothing already
merged was renumbered.

This investigation is pinned to the public `rust-v0.154.0` source for
codex-cli 0.154.0. The [current hooks documentation](https://developers.openai.com/codex/hooks.md)
and [current configuration reference](https://developers.openai.com/codex/config-reference)
describe a moving product surface; source links below pin the claims on which
this decision depends. The investigation did not inspect a production Codex
home, copy credentials, enable native memory against an authenticated provider,
or demonstrate native extraction. Hook probes used an isolated temporary
`CODEX_HOME`; a mock provider, where used, proved request transport only. The
installed plugin inventory was deliberately not enumerated, so this record
makes no claim about production plugin-provided memory surfaces. The exact
probe boundary and transcript are recorded in
[`docs/evidence/codex-memory-129.md`](../evidence/codex-memory-129.md).

### The native writer is global, asynchronous and processes older threads

`features.memories` is a stable feature but
[off by default](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/features/src/lib.rs#L1100-L1105).
When enabled, the only non-test app-server call site starts memory work after a
new input turn has successfully started and a primary environment is available
([`turn_processor.rs`](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/app-server/src/request_processors/turn_processor.rs#L669-L681)).
That path includes standalone execution: `codex exec` starts an in-process app
server and sends `TurnStart`
([`exec/lib.rs`](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/exec/src/lib.rs#L973),
[`exec/lib.rs`](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/exec/src/lib.rs#L1137-L1141)).
The task then checks that the thread is non-ephemeral and a root thread, that
memory is enabled and that the state database exists; it spawns the writer
asynchronously
([`start.rs`](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/memories/write/src/start.rs#L33-L80)).
Generation eligibility is resolved separately: `generate_memories` defaults to
true inside the enabled feature, while `dedicated_tools` defaults to false
([`types.rs`](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/config/src/types.rs#L290-L355)).

The selection query deliberately excludes the current thread. It selects older
idle threads with `cwd_filters: None`, `project_id: None` and memory mode
enabled
([`memories.rs`](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/state/src/runtime/memories.rs#L220-L249)).
The tagged defaults use six idle hours, ten days of maximum age, two rollouts
per pass, up to 256 raw records for consolidation, 30 days since last use, and
a 25-percent remaining-rate-limit threshold
([`types.rs`](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/config/src/types.rs#L48-L53)).
An unavailable rate-limit snapshot does not block generation, so the threshold
is a guard when usage is known, not an authentication requirement.
The current configuration reference documents different rollout and age
defaults; this drift is why source claims here stay pinned to 0.154.0.
The `cwd` recorded in generated Markdown and its `applies_to` rules are useful
provenance and retrieval boundaries; they do not create separate per-project
stores. The writer's selection is CODEX_HOME-wide.

The jobs ledger does retain useful partial attribution. A Phase 1 claim records
the invoking root thread as `worker_id` while the claimed `job_key` identifies
the older source thread
([`memories.rs`](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/state/src/runtime/memories.rs#L165-L278));
Phase 2 likewise claims work with the invoking root thread
([`phase2.rs`](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/memories/write/src/phase2.rs#L234-L240)).
That makes a post-run audit of observed jobs plausible. It is not a durable
per-session completion log: Phase 1 upserts overwrite the worker and timing
fields for a reused job key, and Phase 2 rewrites one global job row
([`memories.rs`](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/state/src/runtime/memories.rs#L735-L768),
[`memories.rs`](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/state/src/runtime/memories.rs#L1170-L1193)).
The asynchronously spawned work is not joined to the invoking thread's
`Stop`, so a same-session gate can run before its eventual writes.

### Neither native storage layer carries ADR 0013's contract

Phase 1 stores one row per source `thread_id` in `stage1_outputs`, with source
update time, raw memory, rollout summary and slug, generation time, usage and
selection fields
([`0001_memories.sql`](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/state/memory_migrations/0001_memories.sql#L1-L12)).
It and the jobs ledger live in `memories_1.sqlite`; source-thread metadata is
read from the separate `state_5.sqlite`. Both filenames resolve under the
configured SQLite home, which defaults to `CODEX_HOME`
([`sqlite.rs`](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/state/src/sqlite.rs#L29-L47),
[`config/mod.rs`](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/core/src/config/mod.rs#L3983-L3988)).
Its strict model output contains only `raw_memory`, `rollout_summary` and
`rollout_slug`
([`phase1.rs`](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/memories/write/src/phase1.rs#L136-L148)).
There is no stable per-fact identity, `home` or user-exemption field in that
schema.

The generated filesystem root is `$CODEX_HOME/memories`
([`lib.rs`](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/memories/write/src/lib.rs#L116-L117)).
Phase 2 materializes the extracted rollout summaries as timestamp/hash/slug
Markdown filenames whose slug can change, and rebuilds `raw_memories.md`
([`phase2.rs`](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/memories/write/src/phase2.rs#L205-L222),
[`storage.rs`](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/memories/write/src/storage.rs#L112-L237)).
It then consolidates and reorganizes the global
`MEMORY.md` and `memory_summary.md`
([`storage.rs`](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/memories/write/src/storage.rs#L30-L77),
[`phase2.rs`](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/memories/write/src/phase2.rs#L148-L186)).
The managed tree can also contain generated `skills/<name>/SKILL.md` with
scripts, templates and examples
([`consolidation.md`](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/memories/write/templates/memories/consolidation.md#L20-L35)).
The consolidation format requires task groups, scope, `applies_to` and rollout
provenance, but it is a rewritten Markdown knowledge base rather than an
independent YAML record per fact
([`consolidation.md`](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/memories/write/templates/memories/consolidation.md#L202-L241)).
Validation checks file shape and summary version; it does not validate
ownership, a durable home or a per-entry schema
([`workspace.rs`](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/memories/write/src/workspace.rs#L50-L81)).
Embedding a custom `home` label in generated prose would therefore provide no
preservation guarantee.
The tree's internal `.git` baseline and `phase2_workspace_diff.md` do not form
an audit log: preparation removes the old diff, and successful consolidation
removes it again before resetting the baseline
([`workspace.rs`](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/memories/write/src/workspace.rs#L10-L47)).

Codex does have an opt-in ad hoc note surface, so the conclusion is not that
all native memory is unwritable. On an explicit user request it can create one
timestamped Markdown note under `extensions/ad_hoc/notes`
([`read_path.md`](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/ext/memories/templates/memories/read_path.md#L117-L123)).
The tool accepts only a filename and note, and writes the note verbatim with
create-new semantics
([`ad_hoc_note.rs`](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/ext/memories/src/tools/ad_hoc_note.rs#L22-L37),
[`local/ad_hoc_note.rs`](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/ext/memories/src/local/ad_hoc_note.rs#L12-L39)).
It supplies no native home or user-exemption metadata. The separate history
notes extension persists virtual notes only within a rollout across context
transitions and uses a backend API, so it is not a local cross-session memory
store that this contract can scan
([`tools.rs`](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/ext/history-notes/src/tools.rs#L27-L28),
[`backend.rs`](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/ext/history-notes/src/backend.rs#L29-L88)).

External-agent import is different again: imported Markdown keeps its source
frontmatter, and a project `scope.json` records cwd, but that metadata must not
be reinterpreted as a native Codex thread identity
([`memory_import.rs`](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/external-agent-migration/src/memory_import.rs#L12-L32)).
When memory use is enabled, Codex reads and token-truncates a nonempty
`memory_summary.md` into developer policy
([`prompts.rs`](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/ext/memories/src/prompts.rs#L23-L50)).
With `dedicated_tools` enabled it also exposes ad hoc write, list, read and
search tools over the same store
([`extension.rs`](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/ext/memories/src/extension.rs#L104-L123),
[`tools/mod.rs`](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/ext/memories/src/tools/mod.rs#L30-L52)).

The `/memories` command offers “Use memories”, “Generate memories” and “Reset
all memories” for the current Codex home
([`memories_settings_view.rs`](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/tui/src/bottom_pane/memories_settings_view.rs#L80-L130)).
No singular `/memory` command appears in the 0.154.0 command enum
([`slash_command.rs`](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/tui/src/slash_command.rs#L27-L148)).
The corresponding config switches independently prevent injection into future
sessions or generation from new threads
([configuration reference](https://developers.openai.com/codex/config-reference#memoriesgenerate_memories)).

> “When `false`, Codex skips injecting existing memories into future sessions.”
> — [Codex configuration reference](https://developers.openai.com/codex/config-reference#memoriesuse_memories)

Those controls can reduce exposure, but they do not attach repo ownership to a
fact that is retained.

### Lifecycle hooks expose partial evidence, not ADR 0013 parity

Codex hooks receive `session_id`, `cwd`, event name and an unstable transcript
path; turn-scoped events also receive `turn_id`. `SessionStart` adds a start
source; plain stdout or JSON `additionalContext` becomes developer context.

> “Plain text on `stdout` is added as extra developer context.”
> — [Codex hooks documentation](https://developers.openai.com/codex/hooks.md#sessionstart)

`PostCompact` adds only a manual/automatic trigger, ignores plain stdout, and
can stop after compaction with JSON `continue: false`. `Stop` adds the latest
assistant message and `stop_hook_active`; JSON `decision: "block"` continues
the turn with its reason as a new prompt, so the loop guard must prevent a
repeat. `SessionEnd` adds only reason `other`, can run after 30 idle minutes,
and is advisory: its output cannot keep the thread open
([hooks documentation](https://developers.openai.com/codex/hooks.md#hooks)).

The jobs ledger can tell a later auditor which invoking worker claimed which
Phase 1 source while that mutable row remains. The hook payload alone cannot,
and hook completion is not the memory task's completion boundary. Memory
consolidation also explicitly drops user, project, plugin and session `Stop`
hooks while retaining managed and executor hooks
([`stop.rs`](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/hooks/src/events/stop.rs#L68-L89)).
An ordinary root `Stop` can therefore observe its own turn and a managed
consolidation `Stop` could observe a different authority surface, but neither
supplies the synchronous per-fact mutation/completion contract ADR 0013 uses.
A readable transcript shows what a session said; it does not prove that a later
global pass extracted a specific fact, merged it with other sources, or
preserved it after another rewrite.

Non-managed hooks also run only after the exact definition hash is reviewed
and trusted; project hooks require the project config layer to be trusted
([hooks documentation](https://developers.openai.com/codex/hooks.md#review-and-trust-hooks)).
That hash covers normalized hook configuration, not the contents of a script
named by an unchanged command
([probe source findings](../evidence/codex-memory-129.md#source-findings)).
`--dangerously-bypass-hook-trust` bypasses that review for one invocation; it
does not persist trust or change event data.
Trust determines whether an auditor runs. It does not add memory provenance to
the hook payload.

The credential-free 0.154.0 probe observed `SessionStart`, two `Stop` calls
whose loop guard changed from false to true, and `SessionEnd`; its 13 assertions
passed and a mutated expected guard failed assertion 5. `PostCompact` was
configured but did not fire, so its behavior above is source evidence only.
The manually created memory fixture reached the request as developer context,
but native memory was disabled and both native memory tables remained empty.
That proves hook transport, not native extraction or attribution
([probe transcript](../evidence/codex-memory-129.md#credential-free-cli-probe)).

## Decision

Do not port ADR 0013's current-session mtime marker and `Stop` gate to Codex.
Treat the absence of an enforceable Codex memory-home contract as an explicit
gap while this ADR is Proposed.

The proposed fallback is an explicit, read-only post-generation audit with an
auditor-owned sidecar. The sidecar will map each reviewed fact record, its
strongest auditable source identity and source revision, and a digest of the
exact content reviewed to:

- one or more durable homes in ADR 0013's `<owner>/<repo>:<path>` or GitHub
  blob form; or
- an explicit user-only exemption when the whole reviewed record is personal.

A Phase 1 row or consolidated section can contain several work and personal
facts. One personal paragraph must not exempt the work facts beside it. Source
identity must come from the strongest native provenance available: a Phase 1
`thread_id` plus `source_updated_at`, an ad hoc note path, or a documented
locator inside a generated artifact. The digest is part of the key. A
consolidation rewrite, renamed ad hoc note or content change therefore
invalidates the earlier attestation and becomes a new finding. The sidecar is
owned by the auditor and stored outside the Codex-managed `memories/` root, not
inside files or git state the native consolidator can replace or reset.

The eventual auditor must report findings without modifying, deleting,
promoting or classifying memory. It must distinguish unsupported or unreadable
state from a clean result, and it must identify the exact Codex version and
store root it inspected. Its verifier must prove that new content, changed
content and a changed source identity all invalidate an attestation before any
hook or registrar is added.

This record does not implement or register that auditor. It also does not
choose a lifecycle hook as its trigger: the reliable post-generation boundary,
sidecar schema, fact granularity, consolidation-entry locator, preservation of
review coverage across reorganizations and safe behavior during a rewrite
remain implementation work. Until those are resolved, even a sidecar prototype
must not claim full enforcement. No current README claim should imply that
Codex memory provenance is enforced.

## Consequences

- **The Claude contract remains stronger.** A Claude note has a stable file and
  explicit frontmatter that a same-session `Stop` hook can inspect. Codex gets
  a documented gap until a separate verifier exists.
- **A later audit can cover automatic and ad hoc memory without pretending the
  two have identical provenance.** Phase 1 has a source thread; ad hoc notes
  have a path but no source thread; consolidated entries may have only textual
  lineage. The sidecar has to preserve those distinctions.
- **A clean audit will be a statement about exact content at one store root and
  one Codex version, not proof that every fact was attributed correctly.** A
  model can combine or omit facts during extraction and consolidation before
  the auditor sees them.
- **Rewrites deliberately cause renewed review.** This is noisier than storing
  a free-form `home` line inside `MEMORY.md`, but it avoids treating stale
  metadata as proof after the content it described changed.
- **Disabling generation is still available as an operator choice.** It closes
  the generation path but also discards the feature's benefit; it is not the
  fleet contract chosen here.
- **Chronicle remains outside the evidence boundary.** It is marked under
  development and off by default in this source version, but this source
  archive did not expose enough of its production persistence path to specify
  an audit target
  ([`lib.rs`](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/features/src/lib.rs#L1130-L1135)).

## Alternatives considered

### Port the Claude `Stop` hook

Rejected for full parity because the selected source is an older thread, the
writer runs asynchronously from a later turn, the mutable job row is not a
durable completion ledger, and consolidation suppresses ordinary `Stop` hooks.
A gate can run before the write it is meant to inspect.

### Gate only auditor-owned ad hoc notes

Viable as a narrower contract because those notes have explicit paths and
create-new writes. Rejected as the answer to issue 129 because it leaves
automatic extraction and consolidation unaudited.

### Audit the worker-attributed jobs ledger

Retained as an input to the proposed audit. Rejected as sufficient by itself
because job rows are overwritten and the single global Phase 2 row aggregates
sources; it can support partial attribution, not stable per-fact review.

### Install a managed consolidation `Stop` hook

Potentially viable under a different authority model because managed and
executor hooks survive consolidation filtering. Rejected for this proposal
because it does not solve asynchronous root completion or provide per-fact
identity, and fleet user configuration does not own the managed hook surface.

### Put `home` inside generated Markdown

Rejected because consolidation owns and rewrites those artifacts, and the
validator has no per-entry extension field or preservation rule. A label that
survives one pass is not an enforceable contract.

### Treat `cwd` or `applies_to` as the durable home

Rejected because they describe where guidance applies, not which committed
file owns it. Selection is global and can combine sources from several repos.

### Audit SQLite alone

Rejected as the whole mechanism. SQLite offers the strongest Phase 1 source
identity, but ad hoc notes live on disk and consolidated content can differ
from raw extraction. It remains one input to a multi-surface audit.

### Automatically promote or delete every finding

Rejected for the same reason as ADR 0013: choosing a repo, destination and
user-only exemption requires judgment. Automatic promotion can publish
personal material; automatic deletion can erase useful context.

## References

- [_agent-guidance issue 129](https://github.com/Adam-S-Daniel/_agent-guidance/issues/129)
- [ADR 0013](0013-a-memory-note-outside-a-repo-names-its-home.md)
- [Codex hooks documentation](https://developers.openai.com/codex/hooks.md)
- [Codex configuration reference](https://developers.openai.com/codex/config-reference)
- [Codex `rust-v0.154.0` source](https://github.com/openai/codex/tree/rust-v0.154.0)
- [Credential-free hook probe and source evidence](../evidence/codex-memory-129.md)
