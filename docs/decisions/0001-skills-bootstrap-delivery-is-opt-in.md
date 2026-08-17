# 0001 — skills-bootstrap delivery is opt-in and double-keyed, not fleet-wide

**Status:** Accepted (2026-08-14)

## Context

`_agent-guidance` was asked to deliver the `skills-bootstrap` SessionStart hook
to the fleet, so ephemeral Claude surfaces (cloud sessions, CI runners) get the
skills registry's bundles without every repo vendoring a copy
(agentskills#54 / #56).

The obvious reading of "deliver to the fleet" is: add a third managed artifact
next to `AGENTS.md` and `CLAUDE.md`, and let the existing dynamic discovery
(`gh repo list` over two owners) carry it everywhere. Five facts, each checked
against the live fleet rather than assumed, say otherwise.

**1. The fleet is 18 repos, not the 8 the handoff was sized against.** The
nightly drift report on the `drift-report/update` branch (2026-07-21) scans 15
in `Adam-S-Daniel` + 3 in `jodidaniel`:

> `4A`, `GHA-bench`, `adamdaniel.ai`, `agentskills`, `agentskills-private`,
> `claude-memory-map`, `cms-platform`, `fastmail-actions`, `jc`,
> `repo-settings`, `rss-inator`, `scratch-claude-001`, `scratch-jules-001`,
> `skills-evals`, `wsl-automation`, `jodidaniel.com`, `scratch-claude-002`,
> `squarespacetemp`

Four are scratch/sandbox repos. Two (`4A`, `jc`) are private and unexamined.

**2. The hook without a lock is not inert — it is a permanent false alarm.**
Measured, running the real hook in an isolated `HOME` with no `skills.lock`
present:

```
skills: DEGRADED — no skills.lock found, looked in <project>/skills.lock
(generate it with scripts/generate_skills_lock.py); any previously-installed
skills in ~/.claude/skills were LEFT IN PLACE (this run never read a lock,
so it cannot say which of them are stale)
```

That verdict lands in `additionalContext` of **every ephemeral session in that
repo, forever**, and its remediation names `scripts/generate_skills_lock.py` —
a path that exists in no consumer. So "push the hook everywhere" was never
"sync one file": it was "push one file to 18 repos, then hand-author 18 locks
anyway", with the reach of a fleet-wide push and none of its benefit.

**3. The lock cannot be fleet-synced, so it cannot be paired with the push.**
It is per-repo by design: `agentskills`' own lock is `adam`-only;
`adamdaniel.ai`'s federates two registries (`agentskills@1e4fe9e` +
`cms-platform@679fb61`). Any canonical lock pushed fleet-wide would flatten the
federated one.

**4. Some targets are wrong by construction, not merely wasteful.**
`agentskills` and `agentskills-private` *author* the hook and the lock — syncing
either back is a source-of-truth clobber. `skills-evals` runs the
skill-propagation probes; installing the bundle into its sessions contaminates
the instrument.

**5. Two repos physically cannot receive it, and finding out the hard way
kills the whole run.** `cms-platform` (`.gitignore:2`, `:37`) and `GHA-bench`
(`.gitignore:20`) deliberately gitignore `.claude/`. `git add` on an ignored
path exits **1**, and `sync.sh` runs under `set -euo pipefail` — verified, the
next line is never reached. A naive `git add .claude/hooks/...` therefore
aborts the *entire two-owner sync* at the first such repo, with a git hint as
the only diagnostic.

Cost, for completeness: roughly 185 tokens of always-on context per bundled
skill, in every session that carries a bundle. The `adam` bundle is 9 skills
(~1.3k); `adamdaniel.ai`'s federated 23 are ~3.1k per ephemeral session.

## Decision

Delivery is **opt-in, allowlisted, and double-keyed**. `sync.sh` writes the
hook and its registration only when **both** hold:

1. the repo is named in `repos.yml`'s `skills_bootstrap.repos` — the *fleet
   operator's* allowlist; and
2. the repo already carries its own `skills.lock` — the *repo owner's*
   declaration of which bundles it installs.

Allowlisting a repo that has no lock is a deliberate no-op with a log line
(`no-lock` in the drift report), never an armed half-install.

Four properties follow, and they are the reason for the shape:

- **`_agent-guidance` has no code path that writes `skills.lock`.** Not even to
  create one. The trap in fact 3 becomes *unrepresentable* rather than avoided
  by care — and it is enforced, not remembered: `sync.sh` refuses to commit at
  all if the lock is ever staged, and `test_sync_bootstrap*` asserts the
  federated fixture stays byte-identical through delivery, re-run and
  hook-drift-repair.
- **Hook-without-lock is unrepresentable**, so fact 2's standing false alarm
  cannot be manufactured.
- **The hook is fetched, not vendored.** `repos.yml` pins
  `Adam-S-Daniel/agentskills@<40-hex>` plus the file's sha256; the sync fetches
  and verifies before writing. One canonical copy stays in the repo whose CI
  shellchecks and tests it, and fanning a hook security fix across the fleet is
  a reviewable one-line pin bump rather than a 46 KB blob diff nobody reads.
- **`.claude/settings.json` is appended to, never overwritten**, as a *separate*
  `hooks.SessionStart` group. Both live consumers already run
  `scripts/setup-hooks.sh` there; adding our command inside their group would
  silently inherit their `matcher` and 30s `timeout`, and a 30s timeout around
  a hook that clones two registries is a hook that fails. The writer parses
  with a real JSON parser, and only writes if re-parsing its own output equals
  the original document plus our one group. An unparseable file is refused
  outright — same posture as an existing hand-written `CLAUDE.md`.

Two asymmetries are deliberate:

- **A drifted hook is overwritten, with no `fix_*` opt-in** (unlike
  `CLAUDE.md`). It has no repo-specific seam and no marker to preserve, so a
  divergent copy is either stale-from-an-older-pin — which must self-heal — or
  hand-edited. A hand-edited copy of a file that fetches and executes
  instruction text with no approval prompt is precisely what should not be
  preserved, and the edit is still in git. The escape hatch is the allowlist.
- **A gitignored `.claude/` is a warning, not a `git add -f`.** Overriding a
  repo's explicit, commented policy from a central sync is a conversation to
  have in that repo, not a flag to pass.

**Starting allowlist: `adamdaniel.ai`, `jodidaniel.com`.** Both have heavy
documented web-session workflows and are the repos #54's acceptance criterion
is actually about. `cms-platform` wants the `adam` bundle but is blocked by its
own `.gitignore`; it is documented in `repos.yml` rather than listed, because an
allowlist entry that can only ever render red is worse than a stated reason.

**The `AGENTS.md` correction ships to all 18 regardless.** The managed
"Skills ecosystem" section told every repo that cloud sessions get no bundle
skills, full stop. That is now half-true, and it is the one change that should
reach everyone — at zero always-on token cost, through the mechanism already
shaped for it.

## Consequences

**Good.** Nothing is armed in a repo nobody has examined. The federated lock
cannot be clobbered by a mechanism that has no writer for it. The fleet-killing
`git add` is caught by a `git check-ignore` probe, mutation-tested: removing
the probe fails 9 assertions and stops the run at the second bootorg repo, with
every repo after it silently unsynced. A repo blocked by its own `.gitignore`
degrades to a warning and the run continues. A digest mismatch disables
delivery for the whole run and fails it, while `AGENTS.md` keeps syncing.

**Costs, honestly.**

- **Adoption takes two commits in two repos** (commit a lock; add an allowlist
  entry) where a fleet default would take none. That is the price of key 2, and
  key 2 is what makes the lock the repo's own statement.
- **The strongest argument for default-ON is unanswered by this ADR and is
  answered by the `AGENTS.md` edit instead:** the managed guidance names
  `finding-unknowns` by name in all 18 repos, so the fleet has been shipping
  references to skills most sessions cannot load. The corrected text now says so
  plainly and tells the reader how to check. If that proves insufficient,
  widening the allowlist is a one-line PR — which is the point of making reach a
  data decision rather than an emergent one. (ADR 0002 takes the other exit for
  the one rule that must never depend on a skill loading at all: it moved into
  the managed guidance itself.)
- **A repo with no ephemeral sessions pays zero tokens**, so the cost argument
  does not reach the scratch repos. What it leaves is smaller but worse in kind:
  an armed latent default that first fires when someone opens a web session in
  an unexamined repo — exactly when nobody is watching.

**Left open, deliberately.**

- **Nothing re-pins a stale lock.** A lock pinned far behind installs cleanly
  and reports `OK` in-session — by design, since `--check` asserts faithfulness
  to the pinned ref, not currency. The drift report's Notes column now prints
  each lock's pins so staleness stops being *invisible*, but re-pinning belongs
  in `agentskills` (it owns the generator and the digests) as a
  `platform-bump.yml`-shaped per-consumer PR. **Do not widen the allowlist past
  what a human can re-pin by hand until that exists.** A live trap for whoever
  builds it: `generate_skills_lock.py` inherits `sources` only when
  *verifying*, so regenerating a federated lock without repeating every
  `--source` silently de-federates it and still exits 0.
- **There is no unregister path.** `sync.sh` has no delete semantics for
  anything. Dropping a repo from the allowlist leaves its hook, registration
  and lock in place, still running; the drift report flags it `unmanaged` and a
  human removes it.
- **The hook's surface guard may be narrower than the surfaces.** It tests
  `CLAUDE_CODE_ENTRYPOINT != "remote"` exactly; a surface exporting
  `remote_web` / `remote_mobile` with no `CLAUDE_CODE_REMOTE_SESSION_ID` would
  silently no-op while reporting "durable session". That is agentskills'
  contract to fix, not this repo's.
- **The drift report cannot verify a federated lock's non-primary half**
  against its source registry; it prints the pins and stops there.
