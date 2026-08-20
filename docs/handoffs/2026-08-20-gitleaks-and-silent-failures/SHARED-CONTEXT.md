# Shared context — read this first, whichever session you are

Hard-won facts from the session that produced these prompts. They are here so
you do not re-derive them, and so you do not trip the same wires. Everything
below was **measured**, not assumed; where something is uncertain it says so.

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

**Also: run the right lane.** cms-platform's pure-fs lane is
`cd e2e && npx playwright test --config=playwright.unit.config.js`. Running the
default config pulls in site-rendering specs that need a built Jekyll site and
fail for reasons unrelated to your diff. In the sandbox checkout, 5 unit-lane
failures are pre-existing `ENOENT`s (`_config.yml`, `package-lock.json`,
`.github/ci-runner/Dockerfile`, an `_posts` fixture) — all untracked and absent.
Verify that claim rather than inheriting it.

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

- **No `concurrency` group on a job publishing a REQUIRED status context** that
  can fire more than once per head SHA. GitHub picks non-deterministically
  between a cancelled and a successful run; when cancelled wins the merge API
  returns `405 Required status check "<ctx>" is cancelled` and nothing overrides
  it. `cancel-in-progress: false` does **not** fix this. Documented in
  `cms-editorial-workflow`'s header with incident PR numbers.
- **A workflow excluded by `paths`/`paths-ignore` emits NO check run at all** —
  a required context then hangs forever ("Waiting for status to be reported").
  A job-level `if:` skip is different: it reports `skipped`, which satisfies
  branch protection. Any narrowing of a required-context workflow needs the
  matching stub-side change.
- **Do not re-add `pull_request: edited`** anywhere (cms-platform#222).
- **Do not narrow `dependabot-comment-sync.yml`'s `push: branches: ['**']`** —
  it exists to suppress GitHub's phantom red zero-job runs.
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
- **Never `git add -A` in a clone another agent may be using.** It happened
  twice; stage explicit paths.
- **Delegated work is done when a verifier exits 0**, not when the report reads
  as finished. Name the command, require the exit code, and require a **negative
  control** — a guard shown only to pass proves nothing.
