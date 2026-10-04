# 0012 — Codex gets the guidance as global user instructions, and the project budget is warned, never blocked

**Status:** Accepted (2026-09-14)

## Context

Claude Code reads a repo's `AGENTS.md` whole. Codex does not.

Measured 2026-09-14 against codex-cli 0.154.0:

- Codex walks `AGENTS.md` (or `AGENTS.override.md`) from the project root down
  to cwd under a RUNNING BYTE BUDGET, `project_doc_max_bytes`, default
  **32768**. The budget is spent across the whole chain, not per file.
- A file that does not fit the remaining budget is **not skipped and not
  reported**. It is CUT at that byte, with one
  `tracing::warn!("project doc exceeds remaining budget; truncating")` in
  `codex-rs/core/src/agents_md.rs` that no ordinary session ever surfaces.
- `codex debug prompt-input` renders the model-visible instructions without a
  model call, which is what made this observable at all. Run in cms-platform it
  showed exactly **32,768** bytes of a **55,788**-byte `AGENTS.md` (a
  feature-branch checkout; the copy on `main` was 71,794 bytes), ending
  mid-heading: `## An unapproved gate holds its concu`. Everything below that
  byte — the whole `## Repo-specific additions` half included — was simply not
  present, and nothing in a normal session says so.
- The GLOBAL file, `~/.codex/AGENTS.md` (or `AGENTS.override.md` there;
  `CODEX_HOME` overrides `~/.codex`), is loaded by a different path —
  `codex-rs/codex-home/src/instructions/mod.rs`, as USER instructions — and is
  **not** counted against that budget.
- Codex has its own lifecycle hooks. Config lives in `~/.codex/hooks.json`
  (user layer) or `<repo>/.codex/hooks.json` (project layer). A project-layer
  file loads only when that repo's `.codex/` layer is trusted, and **every
  non-managed hook must be reviewed and trusted BY HASH**, per layer, via
  `/hooks` in the TUI before it runs. A `SessionStart` command hook receives
  one JSON object on stdin and its plain-text stdout is added to the session as
  developer context — the same delivery shape `fleet-memory.sh` already uses
  for Claude Code.

Fleet state on the same date: all 19 repos are on the stub layout (managed
portion 3,582 B), so the managed half is nowhere near the budget. Two consumer
files are not: cms-platform's `AGENTS.md` is 71,794 B — over — and
adamdaniel.ai's is 32,221 B, 547 bytes under it. Both overruns live entirely in
`## Repo-specific additions`.

So there were two separate problems wearing one number. A repo's own file can
exceed a budget nobody could see; and the ~50 kB of fleet guidance had no way
into a Codex session at all, because the only obvious route — inlining it in
each repo's `AGENTS.md`, which is what this repo did before 2026-08-29 — is the
route that would blow the budget in every repo at once and silently evict each
repo's own additions in the process.

## Decision

1. **The budget is Codex's number, treated as a hard ceiling per `AGENTS.md`:
   32,768 bytes.** `scripts/check-agents-md.sh` gains it as invariant 7, with
   `CODEX_PROJECT_DOC_MAX_BYTES=32768` named identically in `sync.sh` and
   `drift-report.sh`. The managed half is held well below it by this repo's own
   size tests — `agents-md/base.md` ≤ 24 KiB, and a FULL-mode build with every
   section ≤ 28 KiB — so a full-mode repo keeps at least 4 KiB for its own
   additions, and a stub-mode repo (all 19 today) keeps nearly all of it.

2. **A consumer file over budget is WARNED and FLAGGED, never BLOCKED.**
   `sync.sh` emits a `::warning::` naming the repo and the byte count and syncs
   the repo anyway; `drift-report.sh` appends a `codex-truncated: <bytes> >
   32768` clause to that row's Notes. Neither fails the repo, skips the push,
   or opens an issue. The managed half is under budget by construction, so the
   overflow is always in `## Repo-specific additions` — content the sync has no
   business editing and the repo alone can judge. Blocking would withhold the
   managed guidance as a punishment for content we do not own, and would leave
   the repo carrying a STALE managed block above additions that are too long
   either way.

3. **The guidance reaches Codex as GLOBAL USER INSTRUCTIONS, through the same
   hook.** `.claude/hooks/fleet-memory.sh` now writes its marked block to two
   destinations: `~/.claude/CLAUDE.md` as before, and `~/.codex/AGENTS.md` when
   — and only when — `~/.codex` already exists. It never creates that
   directory: an empty `~/.codex` reads as "Codex is set up here" to everything
   that probes for it, this repo included, so a machine without Codex gets
   nothing new. One payload, one version id, one verdict line, two surfaces;
   `~/.codex/AGENTS.md` is outside the project-doc budget, so the guidance
   cannot displace a repo's own file.

4. **The Codex hook is registered at USER level, not delivered per repo.**
   `scripts/register-codex-hook.sh` appends one `SessionStart` group to
   `~/.codex/hooks.json`, mirroring `register-bootstrap-hook.sh`'s posture
   exactly (append never overwrite, refuse an unparseable file with exit 3 and
   no write, semantic re-parse guard, idempotent). The reason is the trust
   model, not taste: Codex records trust per hook definition hash per config
   layer, so a sync-delivered `<repo>/.codex/hooks.json` would cost one review
   prompt in each of 19 repos, each loading only once that repo's `.codex/`
   layer was itself trusted. One user-level entry is trusted once per machine
   and covers every repo opened on it — which is also the right shape for a
   hook whose write is global. The command resolves the repo's own synced copy
   at the git root (`git rev-parse --show-toplevel`, as the Codex docs
   recommend, because a session may start in a subdirectory). Parent-directory
   discovery was added on 2026-09-28, below; without a local fleet hook or a
   capable immediate child hook, it still exits 0 silently.

## Consequences

- **A Codex session on a machine that never ran the registrar — or ran it and
  never trusted the hook in `/hooks` — gets the repo stub and nothing else.**
  That is the same degradation a Claude session gets when the hook fails, and
  the stub says how to tell: `codex debug prompt-input` prints exactly what the
  session loaded, and no `fleet-guidance:` line in it means DEGRADED. The
  registrar cannot close this on the operator's behalf; trust is a human
  action by design, and a script that could bypass it would be a worse thing to
  ship than the gap.
- **Global user instructions apply to EVERY Codex project on that machine**, as
  `~/.claude/CLAUDE.md` already does for Claude Code. `FLEET_GUIDANCE_SKIP`
  therefore governs both surfaces with one flag, and removes an
  already-installed block from both — the reason to opt out is the same reason
  on each, and an opt-out that cleared one surface would be an opt-out that
  looks like it worked.
- **The sync does not touch `.codex/` in any repo**, and this decision is what
  keeps it that way. A future need for genuinely repo-specific Codex config
  would be a new decision, not an extension of this one.
- **The per-file check cannot prove the CHAIN fits.** The budget is spent
  across every `AGENTS.md` from the project root down to cwd, so a repo with
  nested `AGENTS.md` files can still truncate while every individual file
  passes invariant 7. The only nested ones in the fleet today are skills-evals'
  eval fixtures (`evals/guidance-bridge-canary/layouts/*/AGENTS.md`,
  `evals/writing-adrs/*/seed/AGENTS.md` — a few hundred bytes each, loaded only
  when a session starts inside them); the residual is accepted rather than
  chased, and named here so the next reader does not mistake a green check for
  a proof.
- **What remains open**: hosted/cloud Codex environments, where nothing has
  been measured yet and where the registrar has no machine to run on;
  `AGENTS.override.md`, which takes precedence over `AGENTS.md` at both the
  project and the global layer and which nothing here writes, reads or checks
  — a repo (or a developer) that has one is outside every gate this ADR adds.

## Addendum — parent-directory launches (2026-09-28)

Measured with codex-cli 0.158.0 in WSL and an empty `CODEX_HOME`: starting in
a multi-repo parent delivered NOTHING. There was no git root for the registered
command to resolve, and no child repo's own `AGENTS.md` was loaded — Codex
walks from the launch directory's git root down, never into its children;
`--add-dir` does not load them either.

The registered command now falls back to the first immediate child hook that
supports `--workspace`. That hook chooses the freshest child payload by the
existing delivery stamp, with sorted path order breaking ties, then uses the
same normal delivery and installed-block arbitration. After the verdict,
`fleet-workspace:` lists the child repos whose own `AGENTS.md` must be read
before working in them. That list survives opt-out and delivery failure: it
describes instructions the session has not loaded. We list rather than inline
them because 19 repos' files would consume a large context budget, and each
file matters only when working in its repo.

From a parent directory, the registered command runs the hook script of the
first immediate child supporting `--workspace`, so an untrusted child checkout
can run its script just as an untrusted repo can through the existing git-root
path. This widens that trust boundary to the launch directory's immediate
children: do not launch Codex from a parent holding checkouts you do not trust.

Run [`scripts/register-codex-hook.sh`](../../scripts/register-codex-hook.sh)
once per machine, then re-trust the changed definition in `/hooks`. The exact
previous default command is replaced in place, preserving its group, order
and metadata; hand-written variants are the operator's and stay untouched.

## Addendum — size caps raised by 1 KiB (2026-10-02)

Decision 1's two caps move from `agents-md/base.md` ≤ 24 KiB and a full-mode
build ≤ 28 KiB to **≤ 25 KiB and ≤ 29 KiB**; a full-mode repo now keeps at
least 3 KiB for its own additions. Codex's 32768 is untouched. The text above
records the numbers as they were decided.

Why: two user-level preambles that had no repo source (delegation, and one
worktree per coding session) were folded into `base.md` and the file had about
100 bytes of headroom. Raising the cap cost less than trimming hard-won rules.
Evidence, measured 2026-10-02:

- All 19 fleet repos' `AGENTS.md` are stub mode (none contains
  `## Working in these repos`, read through the contents API), so full mode is
  an unused fail-safe and the 28 KiB full-build ceiling guards no live repo.
- `base.md` is delivered to `~/.codex/AGENTS.md` (user instructions, outside
  `project_doc_max_bytes`, decision 3) and `~/.claude/CLAUDE.md`, not to a
  project file.
- The full-build test assumes all seven language sections at once, which no
  repo has.

## Addendum — trust and daemon evidence boundaries (2026-10-04)

Observed CLI: `codex-cli 0.160.0`. The
[evidence document](../evidence/codex-trust-and-daemon-0160.md) records the
version command, the dereferenced `rust-v0.160.0` tag, the source files read,
and two local experiments, for
[issue 181](https://github.com/Adam-S-Daniel/_agent-guidance/issues/181)
and [issue 182](https://github.com/Adam-S-Daniel/_agent-guidance/issues/182).
The Decision above is unchanged. The experiments ran in throwaway profiles
with no credentials; no authenticated model request was made.

**Trust:** an explicit untrusted status bypasses project `AGENTS.md`
discovery while retaining the global instructions, and a project with no
trust entry still supplies its `AGENTS.md` (both observed). Project config
and hooks require trusted status; the instruction guard and the config-layer
guard treat missing trust differently (source). A user-level `hooks.json`
hook did not run until its definition was trusted, and ran under
`--dangerously-bypass-hook-trust` in an explicitly untrusted project
(observed). The fleet hook's own script, and a hook trusted through `/hooks`,
were not exercised, so eligibility is not proof the fleet hook runs. Earlier
measurements in this ADR did not record project trust, and this addendum does
not retroactively establish it.

**Diagnostic correction to Consequences:** the 0.160.0 source shows
`codex debug prompt-input` builds in-process without entering the turn path
that runs SessionStart hooks. Absence of an emitted `fleet-guidance:` verdict
in that command is therefore not evidence of degradation. Its managed global
instruction block can demonstrate what the diagnostic loaded from disk;
it cannot prove the current session's hook ran or what a daemon served.

**Refresh:** the server's global-instruction provider rereads disk when
refreshed, preferring nonempty `AGENTS.override.md` over `AGENTS.md`, and
failed reads can retain the last successful copy (source). On a running
managed daemon, a session opened after the global `AGENTS.md` changed loaded
the new content (observed). Core turn ordering captures its first context
before SessionStart hooks, so a rewrite can miss that initial capture; this
is an inference, not a measured request lag. These findings do not establish
general config or hook-definition freshness, or refresh of an already-open
session.

Both issues remain partial. Still open: the fleet hook after `/hooks` trust
(181), and open-session refresh and the SessionStart-rewrite ordering (182).
