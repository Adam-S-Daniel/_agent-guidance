# 0014 — Dependabot config health is swept centrally, from here

**Status:** Accepted (2026-09-15)

## Context

`claude-memory-map`'s `.github/dependabot.yml` landed broken on commit
`e66b678` (2026-08-10T19:36:21Z, PR #16) and sat that way for 36 days before
anyone noticed — by accident, while looking at something unrelated. GitHub
itself knew: the `dependabot` app posted a
[failing check run](https://github.com/Adam-S-Daniel/claude-memory-map/runs/93574227209)
on that exact commit the moment it landed. Nothing fleet-wide reads Checks, so
nothing ever surfaced it. `cms-platform`'s own `scheduled-run-health` reusable
— which only 10 of the 21 non-archived repos under the two owners actually
call — only ever looks at *that repo's own* scheduled workflow runs in any
case; a broken Dependabot config produces no scheduled run to be silent about,
so it is invisible to that alarm even where it is wired up.

**The measured facts the design rests on** (restated in full, with the exact
shas/urls/dates, in `scripts/dependabot-config-health.js`'s own header —
this section only summarises them):

1. GitHub's `.github/dependabot.yml` check run lands on the commit that
   brought the change onto the default branch — for a merge-commit PR, that
   is the **merge** commit, not the commit a path-scoped commit lookup
   returns. Measured: `cms-platform` `9e44154` (the path commit) carries no
   such check; its PR #369 merge `8ab5799` does. `claude-memory-map`
   `e66b678` happens to be both at once.
2. An invalid config that **replaces** a valid one does not stop update jobs.
   `cms-platform`'s config was invalid from `149755b` (2026-08-10) to
   `acc5df3` (2026-08-19), and update jobs still ran and succeeded on
   2026-08-11 and 2026-08-18. A recent job is therefore not evidence of a
   valid config — the two signals (check validity, job recency) have to be
   read independently.
3. Update jobs are Actions runs under a workflow whose `path` is
   `dynamic/dependabot/dependabot-updates`. A repo whose config never
   validated has no such workflow at all — `claude-memory-map`,
   `_agent-guidance`, `fastmail-actions` and `repo-settings` each measured
   zero runs.
4. Weekly schedules are precise: across 261 successful update jobs on five
   public repos (2026-04-29..2026-09-15) the largest gap between consecutive
   jobs is exactly 7.00 days, and Dependabot's own `cooldown` setting does not
   widen it.
5. SchemaStore's `dependabot-2.0.json` allows `cooldown.semver-major-days` for
   every ecosystem — it would have passed all four broken configs this sweep
   exists to catch.
6. As first measured, no existing fleet App had the reach this needs:
   `agents-md-sync` declared `contents:write`, `pull_requests:write`,
   `metadata:read`; `cms-platform-automation` the same plus
   `workflows:write`. Neither could read Checks or Actions runs, or write
   Issues, in an arbitrary fleet repo.

## Decision

**A daily central sweep, in this repo, over every non-fork, non-archived repo
under both `SYNC_OWNERS`, that files ONE `ci`-labelled tracking issue IN the
affected repo** when either signal is bad:

- **Invalid** — the newest `dependabot` check run on the config's own commit
  (or that commit's default-branch merge commit, per fact 1) concluded
  failure.
- **Silent** — no update-job run within the config's own shortest recognised
  schedule interval, plus a 3-day slack (fact 4's margin above real jitter).

Lifecycle: a finding creates an issue if none is open, comments only the
**fresh** evidence (a hidden `<!-- evidence: ... -->` marker on the issue and
every comment tracks what has already been said, so a repeat run never
re-announces the same failure), and shuts the issue once BOTH signals clear —
never on an unknown or merely-pending read, since either would be the sweep
declaring victory on a guess. Anything this run could not determine (a config
read that errored, a check run that could not be classified, a truncated
workflow-listing page) is UNKNOWN, and any assessed repo left unknown reds the
run's own exit code — the fleet-declared `cron_coverage.fleet` in `repos.yml`
is the denominator every owner listing is cross-checked against, the same
discipline ADR 0003 established for the cron-coverage gate. A private repo's
name and finding text never reach this workflow's own (public) log — only a
count does; the issue itself, in the private repo, is where the real detail
lives.

**The credential is the existing `agents-md-sync` GitHub App, widened —
not a new App.** On 2026-09-15 `agents-md-sync` gained Actions: Read,
Checks: Read and Issues: Read & write (it already held Contents: write,
Pull requests: write and Metadata: read for the AGENTS.md sync), and both
installations (`Adam-S-Daniel` and `jodidaniel`) re-accepted the widened
permission set. This repo already mints a short-lived installation token for
that App per owner in `sync.yml` and `drift-report.yml` — reusing it means no
new private key and no new repository secret exist at all; the sweep's
mint steps (`.github/workflows/dependabot-config-health.yml`) use the same
`vars.APP_CLIENT_ID` / `secrets.APP_PRIVATE_KEY` pair, and down-scope the
token they mint to exactly what the sweep needs
(`permission-checks: read`, `permission-actions: read`,
`permission-contents: read`, `permission-pull-requests: read`,
`permission-issues: write`) via `actions/create-github-app-token`'s
`permission-*` inputs, confirmed present at the pinned sha
(`bcd2ba49218906704ab6c1aa796996da409d3eb1`).

### Rejected alternatives

- **A lane inside `cms-platform`'s `scheduled-run-health`.** Rejected because
  that reusable is invoked by a repo's own **caller workflow**, and only 10 of
  the 21 non-archived repos under the two owners call it at all. Even among
  those, a lane added to the reusable would not reach most of them promptly:
  [cms-platform#424](https://github.com/Adam-S-Daniel/cms-platform/issues/424)
  measured seven of the nine external callers stale, on an older version of
  the caller than the reusable itself. And "no scheduled run in 48h" (that
  reusable's whole signal) cannot express fact 2 at all regardless: a repo can
  run update jobs on schedule while serving them from a broken config the
  whole time.
- **Pre-merge schema validation** (a PR check on `.github/dependabot.yml`
  itself). Rejected on fact 5 — the public schema is not strict enough to
  have caught any of the four real breakages — and because it would need
  per-repo adoption across two owners' worth of repos rather than one
  central sweep.
- **A dedicated new App.** A dedicated `dependabot-config-health` App would
  have given the filed issues a clearer, single-purpose bot identity and kept
  its private key scoped to nothing but this sweep. Rejected in favour of
  widening `agents-md-sync`: a second App means a second private key and a second
  repository secret to provision, install on two accounts, and rotate,
  purely to avoid one App's key being able to do two things — see
  Consequences for what that costs instead.

Discussion:
[cms-platform#429](https://github.com/Adam-S-Daniel/cms-platform/issues/429),
[the implementation decision](https://github.com/Adam-S-Daniel/cms-platform/issues/429#issuecomment-5687542401),
[the credential-reuse record](https://github.com/Adam-S-Daniel/cms-platform/issues/429#issuecomment-5687842061).

## Consequences

- **Issues are authored by `agents-md-sync[bot]`, not a dependabot-specific
  identity.** A reader of a filed issue sees the same bot name the AGENTS.md
  sync and the drift report already use, and has to read the issue body (the
  `<!-- dependabot-config-health -->` marker plus the footer) to tell which
  automation opened it.
- **One App's key now spans more.** `agents-md-sync`'s installation can read
  Actions and Checks and write Issues, fleet-wide, in addition to the
  contents/PR write it already had for the sync. The sweep's own tokens are
  down-scoped at mint time (this repo's own responsibility), but the App's
  *installed* permission ceiling is now wider on both accounts regardless of
  which workflow mints a token from it.
- **`sync.yml`, `drift-report.yml` and `skills-lock-bump.yml` mint this same
  App's tokens WITHOUT `permission-*` inputs** — they ask for "whatever the
  App can do" rather than a scoped subset, which is fine for what they
  already needed, but means all three now carry the wider Actions/Checks/
  Issues reach too, as a side effect of the widening rather than anything
  those workflows asked for. They are unmodified by this change and are not
  rescoped here — narrowing them is a separate, later cleanup if it is ever
  worth doing.
- **~100 API calls/day**, rough order of magnitude for ~19-25 repos across two
  owners, each needing a content read, a commit lookup, a PR lookup, one or
  more check-run reads, a paginated workflow listing, a run lookup, and an
  issue/comment lookup.
- **Detection bounds, not real-time.** An invalid config is caught within
  roughly the sweep's own cadence plus GitHub's cron lag (measured 4-5h on
  daily crons elsewhere in this fleet) — call it ≤ ~29h. A silent config is
  caught within its own threshold plus that same lag.
- **Installation visibility cuts opposite ways for public and private repos.**
  A PUBLIC repo the App is not installed on is still listed and readable —
  `gh repo list` for an owner returns public repos as public data regardless
  of installation — so a gap there is only discovered when a WRITE (filing or
  updating the tracking issue) fails. A PRIVATE repo the App cannot see is
  genuinely invisible to the listing itself, not merely unreadable once
  found. The `cron_coverage.fleet` cross-check catches the first shape for a
  DECLARED repo (a registry name no owner listing returned reds the run); an
  UNDECLARED private repo the App was never installed on is simply not
  covered — nothing here can notice an absence it was never told to expect.
- **An unrecognised or missing schedule interval is UNKNOWN, never healthy or
  silent by default.** A config this sweep cannot compute a threshold for
  reds the run rather than being silently skipped.
- **Transient API errors red the run — there is no retry.** A single flaky
  read anywhere in a repo's assessment turns that repo unknown for this run;
  the next scheduled run tries again.
- **The first live proof is a dispatched dry run after this PR merges** —
  `workflow_dispatch` needs the workflow file on the default branch before it
  can be triggered at all, so nothing here can be exercised against the real
  API before then.
