# Shared context — read this first, whichever session you are

Hard-won facts from the session that produced these prompts. They are here so
you do not re-derive them, and so you do not trip the same wires. Everything
below was **measured**, not assumed; where something is uncertain it says so.

**Corrected 2026-08-20** against
[`_agent-guidance#52`](https://github.com/Adam-S-Daniel/_agent-guidance/issues/52),
which measured several of the claims below and found two of them wrong. Both are
corrected in place and both say what they used to say — §2's lint lane and §6's
required-context rule. See `README.md`, "Corrections pass", for the conventions.

---

## 1. Which GitHub connector you are holding

A session here can see **two** GitHub MCP servers. They authenticate as the
**same person**, so `get_me` cannot tell them apart, and the names do not say
which is which.

| | `mcp__github__*` | `mcp__b26ebb34-…__*` (`github-mcp`) |
|---|---|---|
| origin | session-provisioned; **not** in `ListConnectors` | claude.ai **org connector**; listed, `connected: true` |
| tools | superset | **strict subset** — nothing the other lacks |
| Actions, job logs, check runs | yes | **no** |
| auto-merge, review-thread resolve | yes | **no** |
| reads, PR/issue writes, `merge_pull_request`, `push_files`, `delete_file` | yes | **yes** |
| reach | session's attached repos (`add_repo` widens) | GitHub App allowlist, **independent** of session scope |

Consequences:

- **Everything that verifies CI is `mcp__github__`-only.** A session holding
  only `github-mcp` can merge a pull request but cannot check one.
- **Fewer tools is not less dangerous.** Both merge, push and delete. The subset
  connector's reach cannot be inferred from the session's repo list, so a write
  through it can land somewhere the session was never scoped to.
- **A 404 means "not visible to THIS connector"**, never "does not exist".
  Measured 2026-08-19: `github-mcp` 404s on the private `repo-settings` although
  the account can push there; both read a public non-attached repo fine.

**Prefer `mcp__github__` for everything.** Name the connector when you report a
verification.

---

## 2. "The watch finished" is not "CI passed"

This bit the session that wrote this file, twice.

- `1441 passed` with **exit 1** — the FAILURE lines had scrolled out of `tail`.
- In `cmd | tail`, `$?` is `tail`'s and is always 0.

So: capture `${PIPESTATUS[0]}`, or do not pipe. After any watch, query
conclusions explicitly and report the **parsed** result:

```bash
gh pr view <n> --repo <owner>/<repo> --json statusCheckRollup --jq \
  '.statusCheckRollup[] | (.conclusion // .state) as $c
   | select($c != null and $c != "SUCCESS" and $c != "NEUTRAL")
   | "\(.name // .context): \($c)"'
```

`.conclusion` is a check run, `.state` is a legacy commit status — filter on one
and the other's failures read as clean.

**Also: run the right lane — and this paragraph named the wrong one twice, in
opposite directions.**

> ~~cms-platform's pure-fs lane is `cd e2e && npx playwright test
> --config=playwright.unit.config.js` … In the sandbox checkout, 5 unit-lane
> failures are pre-existing `ENOENT`s … Verify that claim rather than
> inheriting it.~~

**And then the correction overshot.** The first pass at this fixed the lane and
invented a fact doing it: it asserted, in bold, that *"there is no
`playwright.unit.config.js`"* and that a *"made-up config name"* had produced
the 5 failures. Both are false. The file is real and tracked
(`git cat-file -t origin/main:e2e/playwright.unit.config.js` → `blob`, added
2026-08-08 in `e5d51b5`), and `e2e/package.json` binds it —
`"test:unit": "playwright test --config=playwright.unit.config.js"` — with the
package description naming it as the way to run the pure-Node suite *"for
platform development on a bare checkout (no consuming site)"*, which is exactly
the situation the struck paragraph was written for. That correction is recorded
here rather than quietly dropped for the same reason every other one is: a
fabricated citation that merely disappears gets re-derived by the next reader.
#52 itself never claimed the file was missing — its wording is *"the required
lane is `self-ci.yml`'s `node-unit-lints` … not `--config=playwright.unit.config.js`"*.
"Not the lane" became "does not exist" in the writing, with no measurement
between the two.

So the accurate statement is narrower than either version. **`playwright.unit.config.js`
is real and is the documented bare-checkout command; it is simply not the lane the
REQUIRED check runs.** The required lane is **`self-ci.yml`'s `node-unit-lints`**,
which selects specs by an **exclusion DENY list** against the DEFAULT config:

```bash
cd e2e && TARGET=prod PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
  npx playwright test --project=chromium-light --reporter=line ./*.test.js
```

Run **correctly** (with the lane's DENY list applied, as `self-ci.yml` does) the
suite is **0 failed, exit 0 — there are no pre-existing failures.** As measured
on 2026-08-20 it was `1394 passed / 92 skipped`; **do not restate those two
counts as current** — that repo gains specs continually and the numbers had
already moved within the day. The durable assertion is `0 failed / exit 0`; a
count is only ever evidence of what a specific run did.

**The config name never caused the 5 failures, and this is the part worth
keeping.** `playwright.unit.config.js` carries no DENY list either — as measured
on `origin/main` 2026-08-20 it is `testMatch: /\.test\.js$/` over one browserless
project, nothing excluded — so it selects the same set as the bare `./*.test.js`
glob and produces the same build-dependent failures. Those specs are the six
`self-ci.yml` names in `deny=` (`canary-content`, `canary-ondemand-noindex`,
`e2e-posts-public-exclusion`, `playwright-image-drift`, `prod-mutate-fixture`,
`base-collections-skip-meta`); they need a `jekyll build` `_site`, the `_e2e/`
fixtures, or a root `package-lock.json` the machinery repo does not ship, and the
lane excludes them **by design**. Seeing them means you ran a selection with no
DENY list — either config — not that the lane is dirty, which is exactly the
reading the struck text pre-authorised.

---

## 3. Merge, release, bump — the full chain

**Merge with a merge commit — `gh pr merge --merge`.** Squash and rebase are
disabled on fleet repos. The three cms-platform-managed repos keep squash for
the Decap publish chain, but `--merge` works everywhere.

**A change to a cms-platform reusable does not reach the sites on merge.** The
chain is:

> merge → Actions → **Cut release** (`workflow_dispatch`, `vX.Y.Z`) → the release
> job dispatches each consumer's `platform-bump` → the bump PR enables
> auto-merge → lands when green → `deploy-production` takes it live.

Fail-open: a missing dispatch secret leaves that site to its Monday 07:00 UTC
cron. **A consumer picks up changes to the dispatch/auto-merge machinery itself
one release AFTER adopting**, since its caller still runs the previously-pinned
reusable.

`platform-bump` moves **every** version reference atomically —
`platform.lock`, the theme gem, all `uses:@<tag>` pins, every `platform_ref:`
input. `check-platform-pin-consistency.js --require-canonical` fails a partial
bump, which is why Dependabot ignores every cms-platform ref in both ecosystems.

**Cutting a release needs a version-bump PR FIRST.** `release.yml` refuses to tag
a tree that does not already declare the version being cut. Measured: dispatching
`v0.1.86` against a tree saying `0.1.85` failed with
`plugin.json declares version '0.1.85' but this run cuts v0.1.86`. Five things
move together — `plugin.json`, `.claude-plugin/plugin.json`, `AGENTS.md`'s
`Current release:` line, every `uses:@` and `platform_ref:` in
`examples/site/.github/workflows` (32 and 22 occurrences), and
`scaffold/create-site.js`'s `PLATFORM_VERSION`. The last two are asserted by
`e2e/examples-site-pins-current.test.js` in the REQUIRED lane, so skipping them
reds self-CI *after* the irreversible tag step. The guard is good; let it stop
you rather than working around it.

**So: whenever you change something applicable, merge it, cut the release, and
confirm the consumer bumps landed.** A verification or a theory that needs the
change live is not validated until it has propagated.

**A federated skill rename does NOT reach a consumer's `skills.lock`
automatically.** Verified 2026-08-19: each site's lock pins cms-platform as a
FEDERATED SOURCE at an explicit ref (`sources[0].ref`). Neither mechanism
advances it —

- the nightly lock bumper runs `generate_skills_lock.py --repin`, which advances
  only the PRIMARY ref and re-derives digests at each federated source's own
  unchanged pin (the script says so out loud: *"that federated pin is a human's
  to advance"*);
- `platform-bump.yml` moves `platform_ref:`, `platform.lock`, the `uses:@` pins
  and the Gemfile — it contains **no reference to `skills.lock` at all**.

So a rename inside the `cms-platform` bundle sits in cms-platform's `main` while
every consumer lock keeps naming the old skill, indefinitely, until someone
advances that federated ref by hand. Budget for that step explicitly; do not
assume merge-plus-release covers it.

---

## 4. Skills and the claude.ai account store

A centralized skill added or updated here cannot be uploaded to the claude.ai
account store from a cloud session — the upload is laptop-bound, and the API has
**no delete** (ADR 0002).

**The rule for that pending window: create or update an open tracking issue, and
block nothing else.** Do not fail CI, do not add a gate, do not render anything
as broken. It is a normal state.

Current reality, traced through actual exit paths: nothing in CI reads
`account-skills.txt`, so a pending upload leaves every repo green — the
"blocks nothing" half is already satisfied. The "issue created or updated" half
is **done by hand today**; the one purpose-built automation reports
`issue: unavailable` because the fired session has no GitHub access.

---

## 5. The gitleaks mechanics behind several of these tasks

`generic-api-key` fires on a **keyword** + a **separator** + a high-entropy
value.

- keywords: `access auth api credential creds key passwd password secret token`
- separators: `= > := ::= :::= || : => ?= ,` — **`@` is not one**, which is why
  `uses: actions/checkout@<40-hex>` pins can never fire.
- entropy gate 3.5.

Therefore:

- **A name you choose becomes data a scanner reads.** A skill named
  `cms-platform-secrets` put `"cms-platform/cms-platform-secrets": "<64-hex>"`
  into a generated, committed, scanned lock and broke both sites' push lane —
  adamdaniel.ai for 8 consecutive pushes, each a blocked editorial publish.
- **The repo that chooses the name is not the repo that breaks.**
- **A PR structurally cannot see it**: the PR lane scans `base..head`, the push,
  schedule and dispatch lanes scan **full reachable history**.
- **Fix at the source.** A `.gitleaksignore` fingerprint is
  `<commit>:<file>:<rule>:<line>` with repo-unique SHAs, so it cannot be
  propagated at all — copied elsewhere it names a commit that does not exist and
  suppresses nothing while looking like coverage.
- **Suppress by value, never by `paths`** — a `paths` entry skips the file
  before any rule runs (cms-platform#260).

---

## 6. Traps that already cost an incident — do not re-propose

- **NO REQUIRED CONTEXT MAY END `cancelled`.** When one does, the merge API
  returns `405 Required status check "<ctx>" is cancelled` and nothing overrides
  it — not auto-merge, not an explicit merge call. GitHub picks
  non-deterministically between a cancelled and a successful run for the same
  context + sha, so the PR reads all-green and simply never lands.

  **This bullet used to be named after one cause** — *"No `concurrency` group on
  a job publishing a REQUIRED status context that can fire more than once per
  head SHA"* — and naming it that way is what let a second cause ship
  underneath it (cms-platform#285, closed `completed` 2026-08-20; #289):

  - **Cause 1, `concurrency`.** Still true. `cancel-in-progress: false` does
    **not** fix it — GitHub keeps the in-progress run plus only the *latest*
    pending one and cancels the rest, so a same-sha burst still leaves cancelled
    runs behind. **The group is declared on the REUSABLE**, so grepping a
    consumer's caller for `concurrency` finds nothing and the hazard reads as
    absent. Both live offenders had their groups REMOVED in cms-platform
    `v0.1.87` (`visual-regression.yml`, `secrets-scan.yml`); each now carries a
    tombstone comment saying why it must not come back. Verified on
    `origin/main`: *"NO `concurrency:` BLOCK — a deliberate, load-bearing
    ABSENCE."*
  - **Cause 2, `timeout-minutes`.** Four days after the concurrency fix,
    `parity / parity` and `preview-media / preview-media` concluded `cancelled`
    anyway on adamdaniel.ai#3202/#3217, with no group anywhere near them —
    because **GitHub reports a job it killed at its wall as `cancelled`, not
    `timed_out`**. Put the wall on a WORK job whose conclusion no ruleset names,
    and publish the required context from a `needs:` + `if: always()` gate that
    translates. The `always()` matters: a gate whose `if:` omits it SKIPS
    instead of reddening, which is the twin defect a careless version of this
    fix introduces.

  Lint-locked as `e2e/required-context-cancellable.test.js` and its CONSUMER
  sibling — **renamed** from `required-context-concurrency*` for exactly this
  reason. A lint keyed on the presence of a `concurrency` key enforces what it
  says, and what it says is one cause of two.
- **A workflow excluded by `paths`/`paths-ignore` emits NO check run at all** —
  a required context then hangs forever ("Waiting for status to be reported").
  A job-level `if:` skip is different: it reports `skipped`, which satisfies
  branch protection. Any narrowing of a required-context workflow needs the
  matching stub-side change.
- **A reusable-call context is `<caller job id> / <reusable job id>`. The
  workflow's `name:` never enters it.** So a stub whose `name:` is
  *"E2E (docs-only stub)"* still reports `e2e / e2e` — it must, since `e2e / e2e`
  is required and a differently-named context would hang every docs-only PR.
  Worth stating because the opposite was written down as fact in
  jodidaniel.com's `cms-automerge-nudge.yml` header and used to justify a
  short `required_contexts` list; corrected in jodidaniel.com#157 and observed
  directly on adamdaniel.ai#3223 (docs-only PR, heavy lane skipped by
  `paths-ignore`, stub supplied `e2e / e2e` → success, run 32321556637).
  The same rule is why `platform-pin-consistency.yml` publishes
  **`pin-consistency / pin-consistency`** and not its own file name — see
  `A-trigger-narrowing.md`.
- **Do not re-add `pull_request: edited`** anywhere (cms-platform#222).
- **~~Do not narrow `dependabot-comment-sync.yml`'s `push: branches: ['**']`~~
  — OBSOLETE as of 2026-08-20; do NOT act on it.** The trigger this defended is
  gone: `dependabot-comment-sync` regenerated the trailing `# vX.Y.Z` label on
  SHA pins, that convention was retired fleet-wide (agentskills ADR 0004 /
  `_agent-guidance` ADR 0007), and both consumer callers had
  `pull_request_target` and `push: branches: ['**']` REMOVED, leaving only an
  inert `workflow_dispatch`. Kept here, struck rather than deleted, because the
  phantom-zero-job-run rationale was real and would otherwise be re-derived —
  and because re-adding any trigger is the one edit that re-arms the retired
  convention. The callers pin `@v0.1.88`, an immutable tag that still ships the
  reusable AND `scripts/sync-action-pin-comments.sh`, so deleting the reusable
  from cms-platform `main` did NOT disarm them; the missing triggers are the
  whole of the disarm. Worse, the script GROWS a label rather than refreshing
  one — its `USES_RE` captures the comment as an OPTIONAL group `(#.*)?` while
  `build_new_line()` emits one unconditionally — so a re-armed run would write
  labels onto bare pins. The callers themselves stay until the next
  `platform_ref` bump past v0.1.88: deleting one earlier reads as
  `workflow-set: MISSING (platform-dictated)` and reds the required
  `pin-consistency / pin-consistency` check (tracked as jodidaniel.com#161).
- **`semver-*-days` is invalid under `package-ecosystem: github-actions`** —
  a schema error that disables the whole `updates[]` entry. It IS valid for
  npm/bundler.

---

## 6a. The Playwright-install hang, and telling stuck from slow

`e2e-tests.yml` jobs on the two consumer sites intermittently WEDGE in the step
"Install Playwright browser + system deps". Observed 2026-08-19 across ~9 hours.
Recognise it rather than re-diagnosing it:

- **Normal**: 23s to ~11 min for that step. **Wedged**: no `completed_at`, every
  later step still `pending`, no movement across repeated polls, hours elapsed.
  One job was cancelled after **6 hours** in it.
- **It is per-runner-pool, not global.** jodidaniel.com recovered while
  adamdaniel.ai was still hanging, having diverged at the same wall-clock moment.
  So "the other repo is fine" is NOT evidence your repo is fine.
- **It is not your diff.** It hit automation-only `platform-bump` PRs that touch
  nothing a human wrote, on both repos, simultaneously. That is the decisive
  test — find an unrelated PR wedged in the same step.
- **Do not test it by looking for failures on `main`.** These workflows are
  `pull_request`-only and never run on `main` at all, so that check is
  structurally incapable of answering and will mislead you.

Remedy, in order:
1. Cancel and `rerun_failed_jobs` **on the same run** — once. A second wedge is
   environmental, not a flake; stop there.
2. Once the pool is healthy (install steps back to seconds), a **fresh sha**
   clears it: merge the base branch into the PR head. This is also the ONLY fix
   for a required context stuck in `cancelled` — a re-run will not clear that.
3. Cancel superseded runs after pushing a fresh sha. `e2e-tests.yml` deliberately
   carries NO concurrency group (it publishes a required context), so the old
   run will not self-cancel and its wedged jobs keep holding runners.

A check that failed on a superseded sha is harmless — branch protection evaluates
the current head — so do not chase it.

## 6b. Green because nothing was checked — three cases in one day

Every one of these reported success while doing nothing. None was caught by
reading the code; each was caught by deliberately breaking it and watching what
happened.

**1. A flag that was never conditional.** `${{ inputs.push_scan && '' ||
'--no-push-scan' }}` is not a ternary — it is two operators, and GitHub returns
the `||` branch whenever the `&&` branch is falsy. An EMPTY STRING is falsy, so
the opt-out fired unconditionally and the default-branch push scan never ran,
while printing `0 failing push run(s)` from a static default. The line above it
survived only because its truthy branch was a non-empty string. It shipped WITH
a passing test: the test asserted the literal characters were present, and they
were. **A spelling check is not a behaviour check.** Now linted by
`e2e/expression-empty-truthy-branch.test.js`, which found a second, older
instance immediately — `regression-review-reaper.yml`'s `KEEP_SHA` was always
the head sha, so the close path spared one waiting run on every closed PR, in
the workflow whose whole job is reaping them.

**2. A lint that examined zero files.** That new lint's first version passed
cleanly. `listWorkflows()` returns ABSOLUTE paths while `readWorkflow()` takes a
BASENAME and prepends the directory, so every read threw ENOENT — and a blanket
`try/catch` swallowed it as "skip this file". Zero files examined, green. It now
asserts a non-zero examined count and keeps the read outside the `try`, so a
read error surfaces instead of being mistaken for a malformed workflow.

**3. A test fixture that only ever exercised one shape.** `skills-evals`'
`make_registry()` writes lock digests in the BARE form only. When the generator
started emitting `sha256:<hex>`, production broke on all 8 skills at once and
the suite stayed green, because nothing ever fed the arm the shape production
had moved to.

The rules that fall out, and they are cheap:

- **A guard is not known to work until you have watched it fail.** Break the
  thing it guards, confirm it goes red, restore, confirm it goes green. Report
  both exit codes. A guard demonstrated only passing proves nothing.
- **Assert the mutation applied.** A negative control that silently no-ops looks
  exactly like a guard that does not fire. Check the injected text is actually
  in the file before concluding the guard missed it.
- **Assert the work happened, not just that nothing failed.** Count files
  examined, tests run, records processed — and fail on zero. "No errors" and "no
  work" are indistinguishable from the outside.
- **Prefer asserting semantics over text.** A regex match on an expression
  validates spelling; parse the structure and assert the polarity.

## 7. How to work

- **Use workflows and adversarial review.** A finding is not a finding until
  something independent has tried to break it.
- **Pull emergent issues into scope recursively.** If fixing X reveals Y, fix Y
  too, and if Y reveals Z, keep going — do not defer it to a list.
- **Verify claims; do not trust a report.** Three subagent conclusions in the
  originating session were refuted by direct checking, and one of them would
  have sent someone chasing a phantom bug.
- **A count that disagrees with the spec is a stop-and-report**, never a
  rounding difference. In that session a test run reported an unchanged count
  and it turned out there were two separate suites.
- **Re-verify the claims you are WRITING, not only the ones you are
  correcting.** This pack's own corrections pass is the worked example, and it
  is why this bullet exists. It re-derived every stale claim it struck, then
  shipped four new ones that were false or already resolved: a fabricated
  *"there is no `playwright.unit.config.js`"* (§2 — the file is tracked and
  bound to `npm run test:unit`), a §B4a written in the present tense about a
  `@v0.1.85` pin that had been bumped fifteen hours earlier, a §B5 reason
  resting on a `concurrency` group #285 had already removed, and a §B5 table
  presenting five shipped `workflow_dispatch` additions as outstanding work.

  Three properties make this failure mode worth naming separately from "verify
  claims" above:

  - **Two of the four were answerable by one `grep` in the agent's own working
    tree.** Not an API call, not a subagent — `grep -c '^concurrency:'` and
    reading an `on:` block.
  - **The risk was named and then not run.** That pass's own concerns list said
    the v0.1.87 bump *"may land at any time, which would make new §B4a's 'seven
    repos on @v0.1.85' stale"* — and stopped there. A risk you can retire with a
    command is not a caveat to record; recording it instead is how it ships.
  - **Correcting is the moment of highest confidence and lowest scrutiny.** The
    fabricated claim landed in **bold, inside a struck-quote frame** — the pack's
    own notation for "this was checked" — which is precisely what stops the next
    reader checking it. A correction inherits none of the authority of the
    verification that motivated the surrounding edit.

  So: state a claim only if you can name the command that produced it, and
  prefer *"as measured <date>"* to a bare present tense for anything that can
  move. Recorded on #52, which was reopened for exactly this.
- **A tree with a running agent is not committable — not even with explicit
  paths.** "Never `git add -A` in a contended clone" (it happened twice) is the
  weak form of this, and the weak form is not enough. An adversarial reviewer's
  negative-control discipline is *apply a mutation, watch it go red, restore* —
  so a contended tree is, at any instant, possibly holding a deliberately broken
  file. Measured: cms-platform commit `baf742b` snapshotted
  `examples/site/.github/workflows/cms-automerge-nudge.yml` at the exact moment
  a reviewer's NC6 mutation was applied, and shipped a 5-context
  `required_contexts` list missing `e2e / e2e` — precisely the jodidaniel.com#156
  defect that work existed to prevent, planted in the template every new site is
  scaffolded from. It reached a pushed commit; CI caught it one cycle later only
  because a lint for that exact shape happened to land in the same commit.

  **None of the obvious guards help here.** Grepping for a `MUTANT` marker (a
  one-line value swap carries none), `node --check` or a YAML parse (a mutation
  is usually still syntactically valid), an `md5sum` taken seconds before
  `git add` — all clean. Persist the **investigation** instead: an issue, a PR
  body, an ADR. That is the expensive part and the part a fresh session cannot
  cheaply re-derive.
- **Green CI is a necessary condition. The REVIEW is the gate.** Three PRs were
  merged on green CI while their adversarial reviews were still running; all
  three came back `NEEDS_FIX`, two with real defects that then had to be fixed
  forward on `main`:
  - **skills-evals#41** — the dry-run write-guard substring-matches only
    `gh issue create` / `gh issue comment`. An issue write via
    `gh api -X POST .../issues`, or `curl`, hoisted above the bail-out leaves
    the suite green. Reproduced twice.
  - **GHA-bench#53** — a `workflow_dispatch` safety clearance that is a
    point-in-time assertion about *another repo's* `fleet.yml`, comment-only and
    unenforced, while that repo's own `dependabot-auto-merge.yml` states twice
    that it expects to gain a required check.
  - **agentskills#106** — `--check-format`'s failure message says the fix
    "recomputes every digest from the pinned ref" and then prints `--repin`
    **without `--ref`**. `--repin` does not inherit `ref`, so it resolves `HEAD`
    and *advances* the pin (measured: `94cdcc81` → `3f40330`). Latent only
    because the `adam` bundle has not moved since `94cdcc81`.
- **A cross-repo string coupling needs the comment to match ALL its cases.**
  `bump-consumer-locks.sh` greps `^FAILED:` out of `generate_skills_lock.py` to
  decide whether to WRITE. When this was found, `FAILED:` covered three
  conditions — malformed digests, a *missing* `skills` key, and an *empty*
  `skills` map — while the caller's comment claimed only the first. It degraded
  safely, but the coupling was looser than the comment asserted, and the comment
  is what the next reader trusts. **Since fixed at the source** (verified
  2026-08-20): the generator now reserves `FAILED:` for the empty-map case and
  answers missing / non-map with `ERROR:`, and both sides document the contract.
  The class of defect is the durable part — a prefix that is a cross-repo API
  needs its every case enumerated on both sides of the wire.
- **Delegated work is done when a verifier exits 0**, not when the report reads
  as finished. Name the command, require the exit code, and require a **negative
  control** — a guard shown only to pass proves nothing.
