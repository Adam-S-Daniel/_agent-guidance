# 0043 — The owner-only guidance leaves the consumer repos, and the stub stays the only per-repo copy

**Status:** Accepted (2026-11-13, by the owner). It amends
[0012](0012-codex-gets-the-guidance-as-user-instructions.md) (the repo hook as
the Codex route), [0016](0016-the-freshest-delivery-wins-the-shared-global-block.md)
(a byte copy of `base.md` in every checkout) and
[0040](0040-the-repo-stub-keeps-only-the-pointer-and-the-irreversible-rules.md)
(where the stub's degraded pointer leads), and builds on
[0042](0042-the-guidance-hooks-are-opt-in-on-a-durable-machine.md) (the owner
signal). Nothing here is implemented; the steps that would follow acceptance
are listed at the end.

## Context

**What every public clone carries.** The sync delivers, to every fleet repo,
`.claude/hooks/fleet-guidance.md` (a byte copy of `agents-md/base.md`,
26,548 bytes), `.claude/hooks/fleet-memory.sh` (SessionStart),
`.claude/hooks/guidance-load-record.sh` (Stop), `agent-checks-guard.sh`
(PreToolUse) and their `.claude/settings.json` registrations. Measured on
2026-11-10: 17 of the 18 public fleet repos carry the identical payload and
hook; only `acsi-kit` (excluded in `repos.yml`) does not. In
`verify-actions-lock`, a published Action, agent files are 459 kB of a
545 kB tree, although the `repos.yml` comment on `acsi-kit` says it is
"copied by hand instead, as for verify-actions-lock"; it is not excluded and
gets the full delivery. `guidance-load-record.sh` names the private
usage-records destination it uploads to.

**Who the payload is for.** By section (bytes from
`agents-md/eval-coverage.yml`, which `check-guidance-coverage.js` keeps
exact), about 40% of `base.md` is rules an outside contributor needs in these
repos, about 55% is for the owner and his agents only, and about 4% is worth
a second look as disclosure. The proposed boundary is in Decision 1.

**What 0042 changed.** On a durable machine `fleet-memory.sh` now installs
nothing without an owner signal (`FLEET_GUIDANCE_OWNER` on, the marker file
`~/.config/fleet-guidance/owner`, a caller's payload, `--codex-cloud`, or a
hosted session). So a stranger's durable session already never receives the
installed guidance; the files still ship in every clone, and a stranger on
Claude Code on the web still gets the owner's guidance installed in the
hosted container (0042 decision 5). For anyone but the owner, "no verdict" is
now the normal state, and the repo stub is all that session sees.

**The owner already has routes that do not need a consumer repo.**

- *Durable machines.* The `adam-anything-anywhere` plugin's
  `hooks/fleet-guidance/on-session-start.sh` fetches `base.md` at the head of
  `main` (git plus raw.githubusercontent.com, never the REST API), falls back
  to a bundled copy, and from 1.2.2 passes `FLEET_GUIDANCE_OWNER=1`. The
  plugin is at 1.2.2 on the owner's WSL and Windows homes, and the marker
  exists on both.
- *claude.ai cloud.* Observed in the cloud session that wrote this ADR
  (2026-11-13, a project thread, working directory `/home/user`, no
  project-level `.claude/settings.json`): the account-synced
  `adam-anything-anywhere` plugin is present with its `hooks/claude-code.json`
  SessionStart entry. Two SessionStart hooks printed a fleet-guidance verdict:
  hook 1, a user-level `~/.claude/settings.json` entry written by the
  environment, which runs the first `*/.claude/hooks/fleet-memory.sh` it finds
  among the attached checkouts, printed `installed`; hook 4 printed `current`
  for the same version. By elimination (the environment registers one
  fleet-memory entry, the launcher's SessionStart hook is a setup-failure
  notice), hook 4 is the plugin's. The same environment entry also runs the
  first `*/.claude/hooks/guidance-load-record.sh` on Stop. Whether the synced
  plugin's hook runs on every hosted surface (claude.ai Code on the web,
  Slack, Teams) was not checked.
- *Cloud setup scripts.* The owner ran the experiment
  [issue 523](https://github.com/Adam-S-Daniel/_agent-guidance/issues/523)
  depended on: a Claude cloud environment's setup script can write user
  memory that the session then loads. `docs/codex-cloud.md` already has Codex
  Cloud's setup download a reviewed file by commit and SHA-256 before running
  it.
- *Evals.* skills-evals' harness delivers its own payload through the pinned
  hook (`FLEET_GUIDANCE_PAYLOAD`, ADR 0032) and never reads a consumer copy.

**The per-repo stub
([issue 473](https://github.com/Adam-S-Daniel/_agent-guidance/issues/473)).**
A session loads each attached repo's `AGENTS.md`, so the managed half is
loaded once per repo on every model call. The 2026-11-02 audit measured about
20 KB of identical stub in a five-repo thread, roughly $30 to $75 a week.
ADR 0040 then cut the stub to the pointer plus one irreversible-rules
sentence: `stub.md` is 1,318 bytes, and the managed half of a consumer's
`AGENTS.md` is 1,508 bytes (measured in rss-inator, wsl-automation,
adamdaniel.ai and skills-evals on 2026-11-13). A five-repo thread now carries
7.5 KB of managed text, 6.0 KB of it duplicate copies. The repo-specific
halves are larger: wsl-automation's `AGENTS.md` is 29,466 bytes in all,
adamdaniel.ai's 24,181.

The tension between the two issues: issue 523 keeps contributor rules "in
each repo's `AGENTS.md`", and issue 473 wants that file small. Inlined, the
contributor sections (11,867 bytes) would make the managed half about
13.4 KB, about 67 KB across a five-repo thread on every call: more than three
times the 20 KB that opened issue 473.

## Decision

### 1. Split the payload by section, from one source

`agents-md/base.md` stays the single hand-edited file and the owner's full
payload, unchanged in shape: the check value (0035), the eval delivery
(0032), the coverage manifest and ablation (0030) and the touch gate all keep
working on it. Each row of `agents-md/eval-coverage.yml` gains
`audience: contributor | owner`; a build script extracts the contributor rows,
in `base.md` order, into the contributor file (Decision 3), and CI
regenerates and diffs it like `AGENTS.md`. The split is section-granular: a
bullet that must not reach contributors moves to an owner section in
`base.md`.

Proposed boundary (the owner's call; bytes as of this ADR):

| Section | Bytes | Audience |
|---|---:|---|
| (preamble: what this file is, sizes, where it is delivered) | 682 | owner |
| Working in these repos | 1,422 | contributor (move the `adam-writing-style` and "text to paste" bullets to an owner section) |
| Anything you name gets its link | 886 | owner |
| Finding your unknowns | 1,015 | owner |
| Turn lessons into checks, skills and guidance | 705 | owner |
| Workstation layout | 149 | owner |
| Security | 617 | contributor |
| Data exposure in CI and public repos | 1,197 | contributor |
| Read before you start | 295 | owner |
| Automation vs branch protection | 2,201 | contributor |
| Two GitHub connectors, and which one you are holding | 1,283 | owner |
| Agent-made GitHub contributions carry a marker | 622 | owner |
| PR descriptions open with a TLDR | 324 | contributor (agent-checks fails a PR without it) |
| A GitHub 404 means "not authorized", not "not there" | 875 | owner |
| The fleet spans TWO owners, and a scoped search will not say so | 963 | owner |
| "The watch finished" is not "CI passed" | 1,364 | owner (borderline: general CI hygiene; contributor if the owner prefers) |
| A successful `git push` does not mean your commit exists | 1,102 | contributor; **review** the 116-byte `SKIP_SECRETS_SCAN=1` bullet |
| Dependency updates | 534 | contributor |
| A name you choose becomes data a scanner reads | 1,003 | contributor; **review** (the gitleaks keyword list and how a value escapes the rule) |
| Pinning GitHub Actions | 1,153 | contributor |
| A test that can signal can kill every session | 918 | contributor |
| Subagent delegation (model routing) | 2,644 | owner |
| Skills ecosystem | 1,228 | owner |
| Two setup gaps you may close, and must not nag about | 1,970 | owner |
| Git practices | 1,396 | contributor |

Totals: contributor 11,867 bytes (44.7%), of which 1,119 (4.2%) is marked
for review; owner 14,681 (55.3%). This matches the 40 / 55 / 5 estimate in
issue 523.

The source stays public. `_agent-guidance` is public and holds `base.md`;
this decision is about what every consumer clone ships and what loads into a
stranger's session, not about secrecy. Anything in the review rows that
should not be public at all has to leave `base.md`, not only the consumer
repos.

### 2. The owner-only part reaches the owner from the owner's side

No consumer repo carries the payload or a hook that installs it. Each owner
surface gets the full `base.md` from a route the owner installed, and every
route passes 0042's existing signal; no new signal is added.

| Surface | Route | Signal it carries |
|---|---|---|
| Durable machine, Claude Code | the `adam-anything-anywhere` plugin's SessionStart hook (live fetch, bundled fallback), as today | `FLEET_GUIDANCE_OWNER=1` (installing the plugin is the opt-in) |
| Durable machine, local Codex | the same plugin hook writes `~/.codex/AGENTS.md` whenever `~/.codex` exists, on every Claude Code session; `register-codex-hook.sh` is re-pointed from the repo's copy to a launcher that `setup.sh --owner-machine` installs, which runs the plugin's hook | `FLEET_GUIDANCE_OWNER=1` from the plugin hook; the marker for anything that passes nothing |
| claude.ai cloud (Claude Code) | the account-synced plugin's SessionStart hook (observed above), and, as the guaranteed route, the owner's environment setup script, which downloads a reviewed `fleet-memory.sh` by commit and SHA-256 and installs `base.md` from `main` | `FLEET_GUIDANCE_OWNER=1` and a caller's `FLEET_GUIDANCE_PAYLOAD` with its `FLEET_GUIDANCE_PAYLOAD_STAMP`; the hosted signal is also present |
| Codex Cloud | the `docs/codex-cloud.md` setup recipe, changed from running the checkout's `.claude/hooks/fleet-memory.sh --codex-cloud` to the same reviewed download plus fetched payload | `--codex-cloud` |
| skills-evals | unchanged: the harness passes its own payload | `FLEET_GUIDANCE_PAYLOAD` |

Where two routes run in one session (plugin and setup script), 0016's
freshest-stamp rule arbitrates and the second reports `current`. The plugin
already stamps with the fetched commit's committer time; the setup script
does the same.

### 3. What stays in consumer repos

| File / registration | Stays? | Why |
|---|---|---|
| `AGENTS.md` managed half (`stub.md`) | **stays**, ≤ 2,048 bytes as 0040 caps it | the one file guaranteed to load in every session on every harness (Decision 4) |
| contributor rules, as a managed file `.github/fleet-contributor-rules.md` (11,867 bytes today), never imported by `AGENTS.md` or `CLAUDE.md` | **new** | rules any contributor's agent needs, read on demand; `.github/` is outside Jekyll output, holds no repo-owned file of that name, and is where contribution metadata lives |
| `.claude/hooks/agent-checks-guard.sh` + PreToolUse entry | **stays** | enforces the repo's own commit rules, which a contributor needs (0021; 0042 left it unchanged) |
| `.claude/hooks/skills-bootstrap.sh` + SessionStart entry | **stays** | owned by adam-agentskills and opt-in per repo (0001, 0004); a no-op on a developer machine |
| `disableSkillShellExecution: true` | **stays** | 0029 |
| `.claude/hooks/fleet-guidance.md` | **leaves** | it is the owner payload |
| `.claude/hooks/fleet-memory.sh` + SessionStart entry | **leaves** | with no payload beside it there is nothing to install; the owner's routes (Decision 2) carry their own copy |
| `.claude/hooks/guidance-load-record.sh` + Stop entry | **leaves** | it measures whether the owner's sessions loaded the guidance (0042 decision 3) and names a private destination; it moves to the owner's plugin, preferably `adam-private-anything-anywhere` (the private registry) so the destination is named only in a private repo, registered on Stop with 0042's gate unchanged. The claude.ai environment's user-level Stop entry is dropped with it |

The sync removes the three leaving files and exactly the registration entries
it wrote (matched by command string); every other entry, and a
`settings.json` it cannot parse, is left untouched, as for its other writes.
Git history keeps the old payloads. They are public in `_agent-guidance`
anyway, so history is not rewritten; "a fresh clone holds no owner-only
guidance text" means the checked-out tree.

This repo is the source: it keeps `base.md`, `fleet-memory.sh` and
`guidance-load-record.sh` (the tests run them), and drops its own
`.claude/hooks/fleet-guidance.md` mirror and its own fleet-memory and Stop
registrations like every consumer, since the plugin covers its sessions.

### 4. Issue 473: keep option 3, practice option 4, reject options 1 and 2

No option among the four stops the per-repo copy while keeping the guarantee.
A session that opened one repo, on any harness (Claude Code or Codex), with
no hook, has exactly one fleet file in context: that repo's `AGENTS.md`. The
floor therefore has to be in every repo's copy, and a session with N repos
attached loads it N times. The decision minimizes the floor instead of
deduplicating it:

- **Option 3, floor in user memory with a ~1 KB stub: kept.** It is in force
  since 0040 (1,508 bytes per repo). This ADR's split must not reverse it, so
  contributor rules go to an on-demand file (Decision 3), not into
  `AGENTS.md`.
- **Option 4, attach fewer repos: adopted as standing practice** for project
  threads, with no guidance change. It is the only option that also shrinks
  the repo-specific halves, which outweigh the stub (29,466 and 24,181 bytes
  in the two largest measured above).
- **The stub's degraded pointer changes** (the one stub edit): with no
  verdict, or `DEGRADED`, read `.github/fleet-contributor-rules.md` in this
  checkout before non-trivial work; a session working for the repo owner reads
  the full `base.md` at its URL instead and says it ran degraded. The
  irreversible-rules sentence, the 32 KiB note and the check-value request
  (0035) stay word for word. The exact wording gets the one-way-door
  treatment issue 473 asks for: an adversarial round on a degraded session
  before merge.

The residual cost is about 1.5 KB per extra attached repo per call (6.0 KB in
a five-repo thread), accepted as the price of the guarantee.

## Consequences

- A stranger's clone ships the stub, the contributor rules and the commit
  guard; no owner payload and no hook that writes outside the repo. A
  stranger's hosted session no longer gets the owner's guidance installed
  (this closes the gap 0042 decision 5 left open).
- The owner's sessions depend on the owner-side routes. A session where the
  plugin hook fails and no setup script ran shows `fleet-guidance check: none`;
  the stub then sends it to `base.md`. Before 0043 a fleet repo was a second
  route on the owner's machines; after it, a new owner machine without the
  plugin or the marker gets nothing until `setup.sh --owner-machine` runs.
- Local Codex sessions refresh the global block only when a Claude Code
  session or the re-pointed launcher runs; re-pointing the registered command
  costs one `/hooks` re-trust per machine, which 0042 avoided only because the
  repo copy still existed.
- A stranger's first reply still begins `fleet-guidance check: none`: one
  line, kept because the owner's own degraded session cannot be told apart
  from a stranger's at the stub.
- `base.md` remains byte-identical to what the owner's sessions install, so
  0016's "payload equals `base.md`" holds for every owner route; what no longer
  holds is that every checkout carries that copy.
- Edits to the contributor sections reach consumers on the next sync, as the
  stub does; edits to owner sections reach only the owner's sessions. The 0039
  window and the touch gate still apply to both, because both live in
  `base.md`.
- Section granularity means a contributor section's owner-flavored bullet
  ships unless it is moved; the table names the two known cases.
- Done-when for issue 523 becomes: no tracked file in a fresh clone of a
  public fleet repo carries an owner-section heading or the payload files; the
  owner's durable and cloud sessions report a matching check value
  (`check_reported: match` in the Stop hook's records); CI green.

## Alternatives considered

- **Inline the contributor rules in each consumer `AGENTS.md`** (issue 523's
  literal wording). About 13.4 KB per repo and 67 KB per five-repo thread on
  every call, more than three times the 20 KB that opened issue 473; it also
  eats into Codex's 32,768-byte project budget, already exceeded by one
  consumer's own additions (0012). Rejected.
- **Link the contributor rules by URL only, like today's `base.md` pointer.**
  Costs nothing on disk, but a session without network or behind the cloud
  egress proxy cannot read it, and the file would not be versioned with the
  agent-checks the same sync delivers. Rejected in favor of an in-repo file.
- **Put the contributor rules in `CONTRIBUTING.md`.** Repos own that file; the
  sync must never overwrite repo-owned content. Rejected.
- **Keep the hook in consumer repos and have it fetch the payload.** 0016
  keeps the hook network-free, and the hook would still ship in every clone
  and still install into a stranger's hosted session. Rejected.
- **Keep the status quo behind the 0042 gate.** The gate stops writes to a
  stranger's home, but the files still ship in every clone and a stranger's
  hosted session still installs them. That is what issue 523 is for. Rejected.
- **Move `base.md` to a private repository.** Would make the owner text
  secret, but every owner session would need a credential for the fetch (the
  plugin fetches anonymously, and the cloud proxy 403s the REST API for
  unattached repos), and nothing in the payload is measured as needing
  secrecy beyond the 4% review rows. Not decided here; left open.
- **Issue 473 option 1, short stub with the floor only when degraded.** Drops
  the irreversible-rules sentence (159 bytes per copy, about 800 bytes in a
  five-repo thread) and with it the only rules a degraded session that never
  reads `base.md` still sees. Rejected; its mechanism (read a file only when
  degraded) is reused for the contributor rules.
- **Issue 473 option 2, hook-side dedupe** (`claudeMdExcludes` or an
  `InstructionsLoaded`-style mechanism). As documented, both act on whole
  files, so excluding a duplicate `AGENTS.md` also drops that repo's
  `## Repo-specific additions`, which are not duplicates. It is Claude-only,
  Codex keeps every copy, it helps only sessions that pass the 0042 gate, and
  it adds a settings write on a durable home. Rejected.
- **A second owner signal** (for example a cloud-only variable). Issue 523
  rules it out and 0042's set already covers every route above. Rejected.

## Implementation steps (after acceptance; none done here)

In rollout order: every owner route works before any consumer copy is
removed, so no owner session goes without the guidance in between.

1. **Boundary.** The owner accepts or edits the table in Decision 1. Add
   `audience` to every `agents-md/eval-coverage.yml` row and teach
   `scripts/check-guidance-coverage.js` to require it. Move the two owner
   bullets out of "Working in these repos" (a `base.md` edit: touch-gate entry
   in `docs/guidance-impact.md`, 0039 window).
2. **adam-agentskills.** Land
   [adam-agentskills#152](https://github.com/Adam-S-Daniel/adam-agentskills/issues/152)
   (the bundled installer is behind) first. Add the Stop hook to the owner's
   plugin (private plugin preferred) with 0042's gate; add the local-Codex
   launcher to `setup.sh --owner-machine`; drop the fleet-memory and
   guidance-load-record loops from `docs/multi-repo-delivery.md`'s
   environment snippet, keeping skills-bootstrap and agent-checks-guard.
3. **Owner cloud environments.** Setup script downloads the reviewed
   `fleet-memory.sh` by commit and SHA-256 and installs `base.md` from `main`
   with `FLEET_GUIDANCE_OWNER=1`, the payload and its stamp. Update
   `docs/codex-cloud.md` the same way for Codex Cloud, and
   `scripts/check-codex-cloud-context.py` with it. Verify with a matching
   check value in a cloud session with no fleet repo attached, and check
   whether the synced plugin's hook runs on each hosted surface the owner uses.
4. **This repo: the contributor file and the stub.** A build script for
   `.github/fleet-contributor-rules.md` with a regenerate-and-diff CI step;
   the stub's degraded pointer (Decision 4) after its adversarial round;
   `AGENTS_MD_MODE=full` inlines the contributor file, never `base.md`.
5. **This repo: the sync** delivers the contributor file and removes
   `fleet-guidance.md`, `fleet-memory.sh`, `guidance-load-record.sh` and
   exactly its own registrations for them, with tests that foreign entries and
   an unparseable `settings.json` are untouched. Merge only after steps 2 and
   3 are verified on every owner home and environment.
6. **Issue 523 step 5: what assumes the byte-for-byte mirror.**
   `scripts/check-fleet-guidance-mirror.js` (keep its marker-uniqueness check,
   retire the mirror comparison with this repo's mirror);
   `scripts/drift-report.sh`'s payload rows (compare the contributor file,
   and report a leftover `fleet-guidance.md` as drift); the mirror equality
   and size assertion in `test/test-on-hold-label.js`; the stub's 0035
   check-value request and `check-codex-cloud-context.py`'s expected block;
   `scripts/sync.sh`; and the `test/run-tests.sh` tests that copy `base.md`
   into a consumer, which switch to the contributor file where they model a
   consumer.
7. **Issue 523 step 4.** Correct the `repos.yml` comment that says
   `verify-actions-lock` is handled by hand: it then receives the stub, the
   contributor file, the agent-checks runner, CI check and guard, and
   skills-bootstrap per its `skills.lock`. Whether to exclude it is a separate
   question.
8. **Guidance text that describes delivery** (the `base.md` preamble, "Two
   setup gaps…", "Skills ecosystem") is updated in the same window, each with
   its touch-gate entry; this repo's own `AGENTS.md` repo half drops the
   mirror paragraph.
9. **Close issue 473** when step 4's stub change merges; the per-repo cost was
   settled by 0040 and this ADR.

## References

- Issue 523, "Move the owner-only guidance out of the consumer repos":
  https://github.com/Adam-S-Daniel/_agent-guidance/issues/523
- Issue 473, "Stop loading the AGENTS.md floor stub once per attached repo":
  https://github.com/Adam-S-Daniel/_agent-guidance/issues/473
- The owner gate, ADR 0042, merged in
  https://github.com/Adam-S-Daniel/_agent-guidance/pull/522 and
  https://github.com/Adam-S-Daniel/adam-agentskills/pull/149
- Follow-ups that test the gate's assumptions:
  https://github.com/Adam-S-Daniel/_agent-guidance/issues/525 and
  https://github.com/Adam-S-Daniel/_agent-guidance/issues/526
