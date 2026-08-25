# 0010 — The hook pin is re-pinned nightly too, and only ever proposed

**Status:** Accepted (2026-08-25). Extends
[0005](0005-consumer-locks-are-re-pinned-from-here.md) to the second pin, and
deliberately does **not** extend [0006](0006-bump-prs-land-on-a-sweep.md)'s
sweep to it — the *Decision* below says why that asymmetry is the point rather
than an oversight.

## Context

Two pins decide what an ephemeral session actually gets:

| pin | says | moved by |
|---|---|---|
| a consumer's `skills.lock` | which bundles, at which registry commit | `bump-consumer-locks.sh`, nightly, since 0005 |
| `repos.yml`'s `skills_bootstrap.ref`/`sha256` | which **hook** reads that lock | a human editing `repos.yml` |

The second row was the whole problem. `sync.yml` — the workflow that delivers
the hook — has triggers `push` (path-filtered) and `workflow_dispatch`, and **no
cron**. `repos.yml` is in that path filter, deliberately: the filter's own
comment records that while it was missing, "bump the pin to fan a hook fix
across the fleet" produced zero runs. So delivery was event-driven all the way
down, and the event was a person remembering.

**Measured 2026-08-25.** agentskills #131 ("install the union of every
discovered lock in a multi-repo session") merged as `da48d29` at 04:02:42Z. The
nightly bumper re-pinned adamdaniel.ai's `skills.lock` onto that very commit.
Its hook stayed at `f92569e` — "Accept both lock digest shapes", 2026-08-19 —
whose bytes hash to `23bbadc8…`, while the hook on agentskills `main` hashed to
`282edc21…`. adamdaniel.ai was running #131's **lock** against a pre-#131
**hook**.

Three things about that state made it hard to see, and they are why this is an
ADR and not a one-line fix:

- **Nothing was lagging.** The delivered bytes matched the recorded digest
  exactly. `sync.sh` had already done its job; there was no queued work, no
  failed run, no drift for the drift report to print. The pin was *stale*, which
  is not a state any of the existing checks are shaped to notice, because every
  one of them asks "does the fleet match `repos.yml`?" and the answer was yes.
- **The cadences were invisible from either end.** The lock is re-pinned by a
  cron; the hook by a memory. Nothing in the repo stated that the two moved at
  different rates, so the natural reading — "skills delivery is automated" —
  was half true in a way that reads as fully true.
- **It was already the normal state, not a new regression.** The pin had moved
  five times in the six days after delivery shipped (2026-08-14 → 2026-08-19)
  and not once in the six days since. That is the signature of a step that only
  happens while someone is actively working on the thing.

## Decision

**`scripts/bump-hook-pin.sh` runs as a third pass of `skills-lock-bump.yml`, and
proposes the hook pin the same nightly run proposes the locks.** It opens one
pull request on this repo moving `skills_bootstrap.ref` and
`skills_bootstrap.sha256` together.

Four properties, each chosen against a specific way this could go wrong:

**It is keyed on the hook's DIGEST, not on the registry's HEAD.** A re-pinner
that asked "is `ref` the newest commit" would open a PR here every night, since
the registry moves for reasons that have nothing to do with the hook. A fleet
that learns to ignore these PRs is worse than no re-pinner — the same argument
the lock lane's `repo-current` fixture exists to hold. The visible consequence,
stated because it looks like a bug: **`ref` lags the registry's HEAD by design**
and names the commit at which the hook last *changed*. That is the more useful
pin anyway; it is the commit a reviewer wants when asking what this hook is.

**It proposes and stops — no sweep, no auto-merge.** This is the deliberate
asymmetry with 0006, which had the bumper merge its own pull requests. 0006's
argument was a pile: one skill edit is up to ten near-identical one-line diffs,
and a queue of ten is a queue nobody reads. Neither half of that carries here.
The hook pin is **one** file in **one** repo, so there is never a pile — the
script refuses to open a second PR while the first is open. And the blast radius
is different in kind: merging this fans a new **hook** into all ten allowlisted
repos, and that hook is code that runs at the start of every ephemeral session
in every one of them. 0005 drew this line first ("the App's ruleset bypass
exists so `AGENTS.md` can land unattended; this diff is different in kind"), and
0006 moved it for locks specifically. It does not move for the hook.

**It vets the bytes before proposing them.** The path must exist at the target
commit, the file must be non-empty, and it must parse under `bash -n`. A pin
records *where* the hook is and *what it hashes to* and is equally happy
recording zero bytes or a file that dies at line 1 — delivery would then hand
every allowlisted repo a hook that fails in every session, with a correct
digest, so `sync.sh`'s integrity check passes it straight through. `bash -n`
parses without executing, so nothing from the registry runs on the runner.

**The write is a two-line text edit with a semantic guard, not `yq -i`.**
`repos.yml` is ~90% comment by line count and every comment is prose an ADR
points at; an in-place YAML re-serialisation is a whole-file diff in which the
two bytes that matter are invisible. So the script rewrites exactly the two
scalar lines, then re-parses both documents and requires the result to equal the
original with only `skills_bootstrap.ref` and `skills_bootstrap.sha256` changed
— otherwise it writes nothing. That is `register-bootstrap-hook.sh`'s guard, in
the same shape and for the same reason: a successful write provably means "the
old file plus those two values".

## Consequences

**What this makes worse.**

- **A workflow named for locks now moves two different kinds of pin.** Mitigated
  by renaming it (`Bump the fleet's skills pins`) rather than by leaving the
  name lying, but the file is still `skills-lock-bump.yml` and its concurrency
  group is still `skills-lock-bump` — both load-bearing elsewhere and not worth
  churning for a name.
- **One more nightly write path holding a token with push scope.** It is
  narrower than the lock bumper's — one repo, one branch, and a guard that
  refuses to commit anything but `repos.yml` and the hook path that `repos.yml`
  itself names — but it is another one.
- **The bump now carries a 77 KB binary-ish diff, not a two-line one.** This
  repo is dropped from the sync's per-repo loop ($SELF_REPO, 0004 fact 5), so it
  cannot receive the hook it publishes the pin for and carries its own copy,
  which `test_self_hosted_hook_pin` requires to hash to `skills_bootstrap.sha256`.
  The pin and that copy therefore have to move in one commit or the bump PR
  fails its own repo's CI. The cost is that the reviewable part of the diff —
  the two pin lines — now sits beside the whole hook. The alternative, two PRs
  that are each red alone, is worse.
- **`bash -n` is a syntax check, not a behaviour check.** A hook that parses and
  is wrong is proposed exactly like a good one. The review that this decision
  keeps a human in the loop for is the actual defence; the parse check only
  removes the failure mode where nobody *could* have noticed.

**What it leaves open.**

- **Nothing notices a proposal going unread.** The script declines to re-propose
  while a PR is open, which is what keeps the run quiet — and also means a
  forgotten pin PR looks exactly like a run with nothing to do. The drift report
  is the natural place to surface "a hook pin bump has been open for N days";
  it does not do that today.
- **The digest question is asked against one registry.** `skills_bootstrap` pins
  a single registry's hook, which is all the schema can express. A future fleet
  delivering two different hooks would need this decision revisited, not
  extended.
- **A hook change and a lock change still land as separate pull requests**, on
  separate repos, merged at separate times. The window where consumers run a new
  lock against an old hook is narrowed from "until somebody remembers" to "until
  this PR is merged", not closed. Closing it entirely would mean gating lock
  bumps on the hook pin, which couples two lanes that fail independently today.
