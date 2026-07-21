# _agent-guidance

The **guidance layer** of Adam Daniel's agent setup: shared `AGENTS.md` content
and the sync machinery that propagates it into every repo in the account, with a
nightly drift dashboard.

This repo deliberately does **not** carry skills. Reusable skills live in the
canonical registry, [Adam-S-Daniel/agentskills](https://github.com/Adam-S-Daniel/agentskills)
(private/sensitive ones in `agentskills-private`) — consume them from there via
the plugin marketplace or that repo's `setup.sh`. The two-layer split (skills vs
guidance) is documented in the registry's
[`STRATEGY.md`](https://github.com/Adam-S-Daniel/agentskills/blob/main/STRATEGY.md).
The skills that used to live here (`debug-github-workflows`,
`review-bash-ci-reliability`) were promoted into the registry.

## How it works

- `agents-md/base.md` + `agents-md/sections/<name>.md` — the managed guidance
  content, composed per-repo by `scripts/build-agents-md.sh`.
- Consumer repos opt into sections by dropping a `.agents-sync.yml` in their root
  (see `.agents-sync.example.yml`).
- `scripts/sync.sh` (CI: `sync.yml`) scans the account's repos, rebuilds each
  repo's managed `AGENTS.md` portion, preserves anything under
  `## Repo-specific additions`, and pushes the change directly to the
  default branch when content changed. This works because the
  `agents-md-sync` GitHub App holds a declared ruleset bypass on every
  fleet-managed repo — declared as code in
  [Adam-S-Daniel/repo-settings](https://github.com/Adam-S-Daniel/repo-settings)
  (see that repo's ADR 0001). Repos whose branch protection still rejects
  the direct push (the cms-platform-managed sites) fall back to opening a
  PR (`agents-md-sync/update`) and enabling auto-merge on it, left open
  for manual merge only if auto-merge can't be enabled. It also creates
  the `CLAUDE.md` bridge in repos that lack one, warns (in the sync log,
  and in the fallback PR's body) when an existing `CLAUDE.md` doesn't
  import `@AGENTS.md`, and can rewrite such a file when the repo opts in
  via `fix_claude_md: true` — see [The CLAUDE.md bridge](#the-claudemd-bridge).
- `scripts/drift-report.sh` (CI: nightly `drift-report.yml`) writes
  `drift-report.md`, a dashboard of which repos are missing or out of date,
  including a "CLAUDE.md bridge" column (`bridge-ok` / `no-import` /
  `missing`). This tracks **AGENTS.md drift** — it is the guidance layer
  working as designed, not the skill-copy drift that the strategy
  consolidation removed.
- `scripts/sync.sh` / `scripts/drift-report.sh` consult the central
  `repos.yml` registry for repos excluded from sync entirely and for
  `default_sections` applied to repos with no `.agents-sync.yml` of their own.

## The CLAUDE.md bridge

### Why

Claude Code reads `CLAUDE.md`, not `AGENTS.md` — there is no native
`AGENTS.md` support (tracked upstream: anthropics/claude-code#6235, open,
no commitment). So the sync creates a two-line bridge file in every repo it
touches:

```
<!-- Managed by _agent-guidance: bridges Claude Code (which reads CLAUDE.md) to AGENTS.md. -->
@AGENTS.md
```

An existing `CLAUDE.md` is never rewritten without the repo opting in via
`fix_claude_md: true` (see `.agents-sync.example.yml`).

This isn't hypothetical: a `CLAUDE.md` in `adamdaniel.ai` that merely
*linked* to `AGENTS.md` (`See [AGENTS.md](./AGENTS.md) for the agent
guidance.`) instead of importing it left roughly 1,300 lines of managed
guidance completely unread by Claude Code for months
(Adam-S-Daniel/adamdaniel.ai#2545). Nothing failed loudly — the file existed,
it just wasn't a working bridge. That's why bridge status is now surfaced in
the drift report, the sync log, and the fallback PR's body when one exists,
not just checked silently.

### Bridge contract

- `@AGENTS.md` must start its own line, outside code spans and fenced code
  blocks — a fenced example of the syntax is documentation, not a working
  import. (`scripts/bridge-status.sh` enforces exactly this rule.)
- Only in-repo relative imports are reliable: importing an absolute path
  outside the repo triggers an interactive approval dialog, which is
  silently dropped in headless/CI runs.
- Import chains resolve at most 4 hops deep; the bridge here uses exactly 1.
- Imported files must keep the `.md` extension (anthropics/claude-code#18518).
- The HTML-comment header in the bridge file is stripped before injection —
  it's human-only signage and costs no context budget.

### Why not a symlink (decision record)

A `CLAUDE.md -> AGENTS.md` symlink looks simpler than a bridge file. It was
considered and rejected, so this isn't relitigated every time it comes up:

- On Windows without Developer Mode + `core.symlinks=true`, git checks out
  the symlink as a plain text file containing the literal string
  `AGENTS.md` — which is *exactly* the broken no-import state this whole
  mechanism exists to catch, and it happens silently.
- Open upstream bug anthropics/claude-code#66559: Edit/Write refuse to write
  through a symlinked `CLAUDE.md`, breaking `/init` and any agent-driven
  memory edit.
- GitHub's UI and API treat a symlink as indirection, not content — anything
  reading `CLAUDE.md` over the API (including this repo's own drift report)
  would need to resolve it specially.
- The upstream changelog has a track record of `.claude/`-path symlink bugs.
- And no upside: every context where the import fails is a context where
  `CLAUDE.md` isn't read at all, so a symlink buys nothing a plain file
  doesn't already provide.

### Verification

Static checks (`bridge-status.sh`, the drift report) prove the bridge has
the right *shape*. Only an end-to-end probe proves it actually *loads*. The
behavioral canary for that lives in a separate repo: a magic-token eval in
[Adam-S-Daniel/skills-evals](https://github.com/Adam-S-Daniel/skills-evals)
(`evals/guidance-bridge-canary`, skills-evals#5).

Loader behavior isn't stable enough to check once and forget: it changed at
least three times in about a year upstream — the SDK's `settingSources`
default flip (and revert), subagent memory-passing changes, and the
`--add-dir` flag. Run the canary again on Claude Code CLI major version
bumps.

### Watch upstream

anthropics/claude-code#6235 tracks native `AGENTS.md` support. It's open
with no commitment either way. If it ships, the bridge becomes redundant but
harmless — nothing breaks by leaving it in place. The canary eval's
`no-bridge` layout turning visible is the signal to simplify the fleet if
that day comes.

## Required secrets

The sync workflow scans repos across **two GitHub accounts** —
`Adam-S-Daniel` and the `jodidaniel` org (see `SYNC_OWNERS` in `sync.yml`) —
so whatever token(s) back the sync need `contents:write` and
`pull-requests:write` on **all target repos in both accounts**. `sync.sh` and
`drift-report.sh` resolve a **per-owner token** at the top of each owner's
loop iteration: for owner `$ORG` they look for `GH_TOKEN_<OWNER>`, where
`<OWNER>` is `$ORG` uppercased with `-` and `.` mapped to `_` (e.g.
`Adam-S-Daniel` → `GH_TOKEN_ADAM_S_DANIEL`, `jodidaniel` →
`GH_TOKEN_JODIDANIEL`). If that's set it's used for the whole iteration; if
not, they fall back to the plain `GH_TOKEN` captured before the loop started
(restored on every iteration, so one owner's per-owner token never leaks into
another's). The workflows populate `GH_TOKEN_<OWNER>` by minting a short-lived
token per owner from a GitHub App at runtime — **mode (a) below, now active**.
Modes (b) and (c) remain documented alternatives; using them means swapping the
`create-github-app-token` mint steps back for `secrets.*` env references.

### a) GitHub App (active — no rotation, ever)

The workflows run in this mode. One GitHub App (`agents-md-sync`) with
permissions `Contents: read & write`, `Pull requests: read & write`, and
`Metadata: read`, installed on **both** the `Adam-S-Daniel` account and the
`jodidaniel` org with access to every target repo. Its App ID is stored as the
repository **variable** `APP_ID` and its private key as the repository
**secret** `APP_PRIVATE_KEY`.

`sync.yml` and `drift-report.yml` each mint a short-lived installation token
**per owner** at runtime with
[`actions/create-github-app-token`](https://github.com/actions/create-github-app-token)
(pinned to a full commit SHA) — one step per owner, writing into
`GH_TOKEN_ADAM_S_DANIEL` / `GH_TOKEN_JODIDANIEL`. Each token lives ~1 hour, is
scoped to that owner's repos, and is minted fresh every run, so nothing expires
and there is no PAT to rotate. The mint steps are `continue-on-error`, so a
missing installation on one owner only skips that owner's repos; sync hard-fails
only if **neither** token can be minted.

### b) Alternative: two fine-grained PATs (single-owner scope each)

A fine-grained PAT is scoped to a single GitHub resource owner, which maps
cleanly onto the per-owner token resolution above. Create one fine-grained
PAT per account with `contents:write` + `pull-requests:write` on all target
repos, and add them as repository secrets:

- `AGENTS_SYNC_READWRITE_TOKEN_ADAM_S_DANIEL`
- `AGENTS_SYNC_READWRITE_TOKEN_JODIDANIEL`

Optionally add a read-only pair for the nightly drift report (`contents:read`
+ `pull-requests:read`):

- `AGENTS_SYNC_READONLY_TOKEN_ADAM_S_DANIEL`
- `AGENTS_SYNC_READONLY_TOKEN_JODIDANIEL`

GitHub has no API to create or regenerate a PAT, so regeneration on expiry is
manual by design — GitHub emails the token owner before it expires. After
regenerating, update the secret with:

```
gh secret set <NAME> --repo Adam-S-Daniel/_agent-guidance
```

### c) Alternative: one classic PAT with `repo` scope (shared, coarser-grained)

Classic PATs aren't resource-owner-scoped, so a single token can cover repos
across both accounts (as long as the token's owner has access to both). Add
it as `AGENTS_SYNC_READWRITE_TOKEN` (and, for the drift report,
`AGENTS_SYNC_READONLY_TOKEN` with read-only scopes). This is the shared
fallback used by any owner that has no per-owner token of its own — the two
modes can be mixed, e.g. a per-owner PAT for one account and the shared
classic PAT covering the other.

Add the App credentials (or the alternative PAT secrets) under Settings →
Secrets and variables → Actions on this repo — `APP_ID` as a **variable**,
`APP_PRIVATE_KEY` as a **secret**. In App mode, `sync.yml`'s "Verify at least
one installation token was minted" step emits a `::warning::` for any owner
whose token could not be minted (App not installed there) and continues,
skipping that owner's repos; it hard-fails the whole job only if **neither**
owner's token could be minted, since `gh` would otherwise fail opaquely (exit
code 4) partway through the run.

The nightly drift report only reads repo contents, so it mints the same
per-owner App tokens but keeps the default `github.token` as a base fallback
(covering whichever account owns this repo). If an owner's App token can't be
minted, its private repos simply show up as fetch failures in the report — a
workable degraded mode rather than a hard failure.

## Layout

```
agents-md/              # managed AGENTS.md content (base + opt-in sections)
scripts/                # build, sync, drift-report
.github/workflows/      # CI, sync-on-push, nightly drift report
.agents-sync.example.yml
repos.yml               # central exclusion list + default sections
drift-report.md         # generated nightly dashboard
test/run-tests.sh
```
