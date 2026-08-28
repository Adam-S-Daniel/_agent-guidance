# 0004 — skills-bootstrap is adopted wherever sessions happen, and this repo self-hosts

**Status:** Accepted (2026-08-19)

## Context

ADR 0001 made `skills-bootstrap` delivery opt-in, allowlisted and double-keyed,
and started the allowlist at two repos: `adamdaniel.ai` and `jodidaniel.com`.
It closed by holding the line explicitly — *"do not widen the allowlist past
what a human can re-pin by hand"* until something exists to re-pin a stale lock.

Widening was then audited rather than assumed. Six facts came back, and two of
them say the mechanism was not doing what its own documentation claimed.

**1. The delivery trigger never fired on the file that carries the policy.**
`.github/workflows/sync.yml` ran on push to `main` for `agents-md/**`,
`scripts/build-agents-md.sh` and `scripts/sync.sh`. But `sync.sh` reads
`repos.yml` at *runtime*: the allowlist, the hook's pinned `ref` and its
`sha256` all live there. A pure `repos.yml` commit therefore produced **zero
sync runs** and waited for an unrelated change to carry it. That makes one
claim false as written:

> "fanning a hook security fix across the fleet is a reviewable one-line pin
> bump" — ADR 0001

It is a one-line change, but on its own it fires nothing.

What this did NOT break is worth stating, because the neighbouring sentence
looks like the same defect and is not. `repos.yml` also said, of
`jodidaniel.com`, *"the first sync run after it merges arms delivery with no
change needed here"* — and that held: the lock merged 2026-08-16, no edit was
made here, and the next sync run delivered the hook on 2026-08-18 (commit
`a4e4f55`, "Also delivers the skills-bootstrap SessionStart hook"). What the
missing `paths:` entry actually delayed was a **`repos.yml`-only** commit, and
nothing else.

The filter now names every file `sync.sh` reads at runtime plus the workflow
carrying `SYNC_OWNERS`, and `test_sync_workflow_trigger` parses the workflow to
keep it that way — deriving the script half from `sync.sh`'s own references so
the assertion cannot fall behind the rule it states.

**2. A consumer with no `.claude/settings.json` at all still adopts cleanly.**
`sync.sh` *creates* the file when it is absent (verified empirically —
`register-bootstrap-hook.sh` treats a missing file as `{}` and writes the one
group), so nothing about registration is the adopting repo's job. What a repo
needs of its own is exactly two things: a committed `skills.lock`, and a
`.gitignore` that does not block `.claude/`. This does not make adoption
cheaper than ADR 0001 priced it — that price was two commits in two repos, the
lock and the allowlist entry, and both still stand. It removes a step nobody
had to take.

**3. The two repos ADR 0001 recorded as physically blocked are blocked by one
line each.** `cms-platform` (`.gitignore:2`, `:37`) gitignores `.claude/`
deliberately and with an explanatory comment; `GHA-bench` (`.gitignore:20`)
carries a bare `/.claude/` — the comment above it is about workflow files, not
about `.claude/`, so "deliberate, with a comment" describes only the first of
the two. Companion PRs narrow both ignores rather than overriding them from
here — which is the conversation ADR 0001 said belonged in those repos.

**4. Two of ADR 0001's stated reasons did not survive checking.**
`agentskills-private` was grouped with `agentskills` as *"they AUTHOR the hook
and the lock"*. It does not: it authors **private skills**, it will consume an
ordinary `adam` lock like any other repo, and nothing in it is a source of
truth this sync could clobber. `scratch-claude-002` was shelved under *"scratch or unexamined"* on the
strength of its name; it is a live portfolio site whose `main` deploys to
sprites.dev — though not on *every* push: `deploy.yml` filters on `index.html`
and on itself, so a sync commit deploys nothing. A wrong reason is worse than a
missing one — it reads as already decided and survives review.

**5. `_agent-guidance` can never receive delivery.** Both scripts drop
`$SELF_REPO` before their per-repo loop (`grep -v "/${SELF_REPO}$"`, `sync.sh`
and `drift-report.sh`). So this repo cannot be delivered to, and never appears
in the drift report's bootstrap table — the same structural gap ADR 0002 found
for `AGENTS.md`, in a second artifact.

**6. `exclude:` wins over the allowlist, and the contradiction is what goes
unsaid.** Excluded repos are filtered out before the allowlist is consulted, in
both scripts. Each run does print `<repo> — excluded by repos.yml`; what
neither script ever says is that an allowlist entry was ignored. A repo named
in both keys is therefore an unchecked contradiction that reads as a decision.
`check-cron-coverage.js` refuses the equivalent `fleet`/`out_of_scope` overlap
outright (exit 2); nothing enforced the same thing here.

## Decision

**Widen the allowlist from two repos to ten, and self-host the hook in this
repo.** The question that decides membership is now *does anyone open an
ephemeral session here* — not *is it safe*, which two repos already answered.

The ten: `adamdaniel.ai`, `agentskills-private`, `claude-memory-map`,
`cms-platform`, `fastmail-actions`, `GHA-bench`, `jodidaniel.com`,
`repo-settings`, `scratch-claude-002`, `wsl-automation`.

**Two of the ten hold key 2 today.** `adamdaniel.ai` and `jodidaniel.com` carry
a committed `skills.lock`; the other eight do not, and a companion PR per repo
commits one (key 2 stays theirs) — `agentskills-private#8`,
`claude-memory-map#19`, `cms-platform#275`, `fastmail-actions#8`,
`GHA-bench#49`, `repo-settings#17`, `scratch-claude-002#15`,
`wsl-automation#9`. Until each merges, that entry is **inert, not wrong**: the
sync logs "no skills.lock in the repo yet — delivery withheld" and skips, and
the drift report cell reads `no-lock`. Widening now rather than after is what
makes each companion merge the LAST step for its repo, with nothing left to
change here.

That includes the two blocked ones. `cms-platform#275` and `GHA-bench#49` each
carry the narrowed `.gitignore` **and** the repo's first lock in one PR, so
neither ever renders `blocked` on the way: `drift-report.sh` tests the lock
before it probes the ignore, and `blocked` is only reachable once a lock
exists. Both read `no-lock` like the other six, then `ok`.

Everything left out is left out for a stated reason, recorded per repo in
`repos.yml`: `agentskills` authors the hook and self-hosts it; `skills-evals`
and `scratch-claude-001` are measurement instruments the bundle would
contaminate (the latter's own SessionStart hook installs the marketplace, and
`E1-HOOK-RESULT.md` / `E1-CLOUD-RESULT.md` are its readings); `4A`, `jc`,
`rss-inator`, `scratch-jules-001` and `squarespacetemp` are dormant — no human
commit since 2026-03, or since 2024-10 for `jc`, and everything after that is
the `AGENTS.md` sync bot; forks and archived repos are never discovered at all.

**This repo self-hosts, and is deliberately NOT allowlisted.** It now carries
its own `skills.lock`, the hook at the pinned ref, and a SessionStart
registration written by `scripts/register-bootstrap-hook.sh` — the same shape
every consumer gets, produced by the same writer rather than hand-authored. An
allowlist entry is refused on the strength of fact 5: it could not deliver
anything, could not be reported on, and would tell the next reader that the
sync maintains this hook, which is exactly the belief that lets a stale copy
sit. What maintains it instead is CI, on all three parts of the adoption,
because any one of them alone is a dead adoption: `test_self_hosted_hook_pin`
asserts the committed hook's sha256 equals `skills_bootstrap.sha256`, and
`test_self_hosted_registration` asserts that `.claude/settings.json` still
registers that hook (an unregistered hook never runs) and that `skills.lock` is
still present and readable by it (a lockless hook prints `skills: DEGRADED`
into every session forever). All offline. Those assertions are the **whole** of
the guard on a file that executes instruction text at session start with no
approval prompt.

**ADR 0001's hold is lifted, and by a specific thing.** A companion PR adds the
re-pinner: a `--repin` mode in `agentskills`' `generate_skills_lock.py` that
inherits registry, bundles and `sources` from the lock already in the repo and
advances only the primary ref, plus a fleet workflow that opens a per-consumer
PR — `platform-bump.yml`-shaped, as 0001 specified. Inheriting `sources` is not
incidental: it is the live trap 0001 named, where regenerating a federated lock
without repeating every `--source` silently de-federates it and still exits 0.

## Consequences

**Good.** The keys still belong to different people: the allowlist is the
fleet operator's, the lock is the repo's, and no code path here writes a lock.
A `repos.yml` edit now actually reaches the fleet, so the pin bump ADR 0001
described works for the first time. The overlap in fact 6 became a suite
assertion instead of an invariant nobody checks, and this repo stopped being
the one place the fleet's own mechanism could not see.

**Costs, honestly.**

- **The widening and the re-pinner land as separate PRs, and this one may land
  first.** If it does, then as each companion lock merges, up to ten repos hold
  locks that only a human re-pins — the state the previous two were already in,
  five times wider, for as long as the gap lasts. It is not a regression (a stale lock installs cleanly and
  reports `OK`; `--check` asserts faithfulness to the pinned ref, not currency)
  and the drift report's Notes column prints each lock's pins so staleness stays
  visible. It is still the ordering risk, and the mitigation is to merge the
  re-pinner promptly rather than to trust that nobody notices.
- **Always-on context, in eight more repos.** Roughly 185 tokens per bundled
  skill in every ephemeral session that carries a bundle — about 1.5k for an
  `adam`-only lock's 8 skills, about 4.3k for a federated 23 (`adamdaniel.ai`,
  `jodidaniel.com`). Both figures are computed from that rate; ADR 0001 quoted
  ~3.1k for 23 skills, which does not reconcile with 185 and is not carried
  forward. That cost is the entire argument for opt-in, and widening spends it
  deliberately rather than retiring it.
- **The fleet guidance had to change with it.** `agents-md/base.md` told all
  ~20 repos that "most repos have not adopted" — a description of the two-repo
  world that goes stale as the companions merge, in the one file that ships
  everywhere. It now states the SHAPE instead of a headcount: a repo holds both
  keys, or is mid-adoption holding one, or is deliberately out for a named
  reason, and `skills.lock` is what tells you which. Deliberately no number, so
  the next widening or narrowing does not silently falsify it — and the
  always-on-context cost stays in that bullet, because it is the reason the
  mechanism is opt-in at all, and a draft that asserted adoption was "the norm"
  had dropped it.
- **`repos.yml` now carries per-repo prose that can rot the way fact 4's did.**
  The mitigation is that the reasons are specific enough to be falsified —
  "it authors private skills", "its `main` deploys to sprites.dev" — rather
  than categories like "scratch", which is the shape that rotted. Falsifiable
  cuts both ways and is supposed to: the first draft of that second reason said
  "deploys on every push to `main`", and review checked `deploy.yml`'s own
  `paths:` filter and corrected it.

**Left open, deliberately.**

- **`agentskills#87` (a `sha256:` prefix on lock digests) is not needed for this
  widening.** The claim is scoped to what was measured, which is `adam`-only
  locks: gitleaks' `generic-api-key` rule wants a secret-ish keyword near the
  high-entropy capture, and an `adam`-only lock has no keyword-bearing line, so
  a repo adopting one needs no gitleaks change. All eight companion PRs commit
  `adam`-only locks, `cms-platform#275` included — which matters because
  `cms-platform` owns the skill literally named `cms-platform-secrets` and runs
  gitleaks itself. A FEDERATED lock is the other case and is not hypothetical:
  `jodidaniel.com`, one of the ten, could not commit its federated lock without
  a `.gitleaks.toml` allowlist entry (merged 2026-08-16 in its PR #134). So the
  issue stays relevant the moment one of these eight federates, and is not a
  blocker today.
- **The instrument that would verify these adoptions is down.** `skills-evals`'
  propagation probes are red (`skills-evals#23`), so "the bundle actually
  loaded in a cloud session in repo X" is currently checked by reading the
  session-opening `skills:` verdict by hand, not by a probe. That is why the
  probe repo stays off the allowlist regardless — fixing it must not require
  trusting the thing it measures.
- **There is still no unregister path.** Dropping a repo from the allowlist
  leaves its hook, registration and lock in place and still running; the drift
  report flags `unmanaged` and a human removes it. Ten repos make that more
  likely to matter than two did.
