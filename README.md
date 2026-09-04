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
  consolidation removed. Per repo-settings' ADR 0001, this generated data
  never lands on the protected default branch — the old standing-PR model
  is gone — so the report is force-pushed nightly to its own unprotected
  results branch, the same pattern skills-evals uses for `eval-results`:
  [`drift-report-latest`](https://github.com/Adam-S-Daniel/_agent-guidance/blob/drift-report-latest/drift-report.md).
- `scripts/sync.sh` / `scripts/drift-report.sh` consult the central
  `repos.yml` registry for repos excluded from sync entirely, for
  `default_sections` applied to repos with no `.agents-sync.yml` of their own,
  and for the `skills_bootstrap` allowlist + pin (see
  [The skills-bootstrap hook](#the-skills-bootstrap-hook)).
- **This repo's own `AGENTS.md` is a committed artifact**, not a synced one:
  `sync.sh` excludes its own repo (`SYNC_SELF_REPO`), which left the repo where
  the guidance is written as the one repo whose agents never read it. It is
  rebuilt from `agents-md/` and diffed by CI ("Self-guidance is current"), so a
  `base.md` edit that forgets to regenerate it fails the build — and a PR that
  changes `base.md` shows the exact text the fleet is about to receive, in the
  same diff. Regeneration command: see `AGENTS.md`'s own "Repo-specific
  additions" section. Reasoning:
  [`docs/decisions/0002`](docs/decisions/0002-unconditional-rules-live-in-the-guidance-not-a-skill.md).

### Section manifest

[`agents-md/eval-coverage.yml`](agents-md/eval-coverage.yml) is one row per
`##` heading in `agents-md/base.md` and per file under `agents-md/sections/`
(each of those is a single `##`). A heading has no identity but its own
wording, and wording changes — so each row carries a stable `id` alongside
the current `heading` text, and `id` never moves when a heading gets
reworded. Every row is `gap` (tracked, no eval yet), `covered` (names a
[skills-evals](https://github.com/Adam-S-Daniel/skills-evals) fixture path),
or `skipped` (names a `reason` and a `since` date).
[`scripts/check-guidance-coverage.js`](scripts/check-guidance-coverage.js)
(CI: the "Section manifest covers every guidance heading" step in
[`ci.yml`](.github/workflows/ci.yml)) is the graduation gate: it parses the
real markdown with `markdown-it` (never a line scan — a fenced code block can
contain a `## ` that is not a heading, and a line scanner cannot tell the
difference) and fails when a
heading has no row, a row's heading no longer exists anywhere (a stale row,
reported with the nearest current heading as the likely rename target), a
`covered` row's fixture is missing, a `skipped` row has no `reason`, or a
row's generated `bytes` — the section's byte extent, regenerated with
`--write-bytes` — has drifted from the real file. A section added with no row
is red, naming the heading and the two ways to close it.

## The skills-bootstrap hook

The sync can also deliver `.claude/hooks/skills-bootstrap.sh` — a
`SessionStart` hook from the
[agentskills](https://github.com/Adam-S-Daniel/agentskills) registry that
installs a repo's declared skill bundles into **ephemeral** Claude surfaces
(cloud sessions, CI runners). It no-ops on a developer's machine, where the
marketplace install stays authoritative. This closes the gap where cloud
sessions get no plugins from repo-declared settings, without every repo
vendoring a mirror of the registry.

**It is opt-in, and the default reach is a deliberate decision, not whatever
repo discovery happens to do.** Delivery requires *two independent keys*:

1. the repo is listed in `repos.yml`'s `skills_bootstrap.repos` — the fleet
   operator's allowlist; **and**
2. the repo already carries its own `skills.lock` — the repo owner's
   declaration of which bundles it installs, at which pins.

Allowlisting a repo that has no lock is a no-op with a log line, not a
half-install: the hook *without* a lock is not inert — it prints a permanent
`skills: DEGRADED — no skills.lock found` verdict into every ephemeral session
of that repo, naming a generator script no consumer has.

**The sync never writes `skills.lock` — not even to create one.** Locks are
per-repo and some federate several registries (adamdaniel.ai's carries both
`agentskills` and `cms-platform`), so a fleet-wide writer would eventually
flatten someone's declaration. There is deliberately no code path for it, and
`sync.sh` refuses to commit if that file is ever staged.

Mechanics:

- The hook is **fetched, not vendored**: `repos.yml` pins the registry, an
  immutable commit, and the file's sha256; the sync verifies the digest before
  writing. Fanning a hook fix across the fleet is a one-line pin bump. A digest
  mismatch disables delivery for the run and fails it — `AGENTS.md` still syncs.
- `.claude/settings.json` is **appended to, never overwritten**, as a separate
  `hooks.SessionStart` group, so an existing `scripts/setup-hooks.sh` entry
  keeps its own matcher, timeout and position. An unparseable file is refused
  rather than rewritten. Registration is idempotent
  (`scripts/register-bootstrap-hook.sh`), and `scripts/bootstrap-status.sh` is
  the shared classifier — `registered` / `no-entry` / `unparseable` /
  `missing` — used by both the sync and the drift report.
- A hook that **drifts** from the pin is overwritten (it is machinery with no
  repo-specific seam; the escape hatch is the allowlist, not a `fix_*` flag).
- A repo that gitignores `.claude/` is **warned and skipped, never
  `git add -f`'d** — `git add` on an ignored path exits 1, which under
  `set -euo pipefail` would abort the whole fleet run.
- The drift report gains a `skills-bootstrap` column (`ok` / `no-entry` /
  `drifted` / `missing` / `no-lock` / `unmanaged`) and prints each lock's pins
  in Notes — the only thing in the fleet that surfaces a stale lock, since a
  stale one installs cleanly and reports `OK` in-session.
- The drift report also reads the registry in **both directions**, since it is
  the only thing here that discovers what the account actually holds: a repo it
  found that neither `skills_bootstrap` key classifies, and a name those keys
  claim that no owner returned. Each is a section of the report plus its own
  sidecar — `drift-report-skills-unclassified.txt` and
  `drift-report-skills-orphans.txt` — which `drift-report.yml` turns into a
  `ci`-labelled issue, because a 06:00 cron notifies nobody. The second never
  concludes "deleted" (a private repo answers 404 exactly like a removed one)
  and is **withheld** rather than written empty on a run where any owner's
  listing failed, so a partial enumeration can neither raise it nor close it.

Full reasoning, including the repos deliberately left off the allowlist and
what this leaves unsolved:
[`docs/decisions/0001-skills-bootstrap-delivery-is-opt-in.md`](docs/decisions/0001-skills-bootstrap-delivery-is-opt-in.md),
then [`0004`](docs/decisions/0004-skills-bootstrap-adopted-where-sessions-happen.md)
for the widening to ten repos and why this one self-hosts instead of being
allowlisted.

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
repository **variable** `APP_CLIENT_ID` (the App's Client ID -- not its
numeric App ID; the action's `app-id` input is deprecated) and its private
key as the repository **secret** `APP_PRIVATE_KEY`.

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
Secrets and variables → Actions on this repo — `APP_CLIENT_ID` as a **variable**,
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

## Cron coverage

A `schedule:`-triggered workflow fails silently — no PR goes red, nothing
notifies. This repo's own `drift-report.yml` was red for 26 consecutive nights
before anyone noticed. `scripts/check-cron-coverage.js` is the gate that says a
repo running crons actually calls cms-platform's `scheduled-run-health`
reusable, and that the caller it has can fire and can file.

Two forms, answering two different questions:

```bash
# "Is THIS repo covered?" — what ci.yml runs. A runner has exactly one repo
# checked out, so this is the form that can exist in CI at all.
node scripts/check-cron-coverage.js

# "Is the FLEET covered?" — every repo repos.yml names under
# cron_coverage.fleet, resolved against a disk root. A named repo that is not
# there, or is not fully checked out, is an ERROR: absence is never a pass.
node scripts/check-cron-coverage.js --repos-root /home/user

# …or an explicit subset, on purpose.
node scripts/check-cron-coverage.js --repos-root /home/user \
  --require _agent-guidance,skills-evals
```

The gate is offline by design (pure filesystem, no network), so it can only
know the fleet `repos.yml` declares. `drift-report.sh` is the half that
discovers, and it flags any repo in either owner that neither
`cron_coverage.fleet` nor `cron_coverage.out_of_scope` classifies — so the list
can go stale, but not quietly. See
[ADR 0003](docs/decisions/0003-cron-coverage-is-fleet-listed.md) for why the
fleet is declared rather than inferred from whatever happens to be cloned.

## The weekly centralization audit

Every workflow in this repo answers a mechanical question — does the managed
block match a fresh build, is the hook pin current, is each `skills.lock`
re-pinned. None of them answers the one that decides whether centralization is
actually working: **is this paragraph, sitting under one repo's
`## Repo-specific additions`, fleet-general?** That is a judgement about
meaning, and it is made against ~1,500 lines of repo-specific prose spread over
eighteen repos.

So it runs weekly as a **Claude Routine** in Adam's account rather than as a
workflow here. The Routine's prompt is deliberately thin and points at the
spec, which lives in this repo so it can be reviewed and corrected by pull
request:

- [`docs/routines/guidance-centralization.md`](docs/routines/guidance-centralization.md)
  — the spec: the procedure, the measured traps, the dated baseline.
- [`docs/routines/guidance-centralization.routine.md`](docs/routines/guidance-centralization.routine.md)
  — the Routine's own configuration, captured from the claude.ai Routines
  API by [`scripts/capture-routine.py`](scripts/capture-routine.py) so that an
  edit made on claude.ai arrives here as a diff rather than as a difference
  against nothing. Configuration only: runtime state is deliberately excluded
  and identifiers are redacted, both for reasons the script's header gives.

It audits three things — that nothing multi-repo-applicable is stranded in one
repo (and that centralized content is not still duplicated locally), that every
repo receives `base.md` or has a written exclusion, and that section opt-ins
fit the repo. If it changes anything, it drives propagation and verifies the
change reached each consumer.

Two things it is explicitly told **not** to do, because both are tempting and
both are wrong: trust the nightly drift report without re-checking against the
repos (its `Has marker` column is wrong for `AGENTS.md` files over ~48 KB, and
that error cascades into a false `drift-detected`), and mass-apply the
`agents-md/sections/` files to every repo that happens to contain a matching
file extension.

## Layout

```
agents-md/              # managed AGENTS.md content (base + opt-in sections)
scripts/                # build, sync, drift-report, lock bump, cron
                        #   coverage, status, routine capture
docs/decisions/         # ADRs (start at the README there)
docs/routines/          # specs for the scheduled Claude Routines that audit
                        #   what no workflow here can check
.github/workflows/      # CI, sync-on-push, nightly drift report, daily
                        #   consumer-lock bump, cron health
.agents-sync.example.yml
repos.yml               # exclusions, default sections, skills-bootstrap pin,
                        #   cron-coverage fleet + out-of-scope
AGENTS.md               # GENERATED from agents-md/ — this repo's own copy
CLAUDE.md               # the bridge that makes AGENTS.md load here too
.claude/                # self-hosted skills-bootstrap hook + its registration:
                        #   the sync skips this repo, so nothing delivers here
skills.lock             # which bundles THIS repo installs (never written by
                        #   the sync — see repos.yml's skills_bootstrap block)
test/run-tests.sh
```
