# Session B — make the remaining silent failures loud

Read `SHARED-CONTEXT.md` first. Use workflows and adversarial review. Pull
emergent issues into scope recursively. Merge what you land; cut a release and
confirm consumer bumps if you touch a cms-platform reusable (SHARED-CONTEXT §3).

The theme: several things in this fleet fail without anyone finding out. One of
those gaps was closed (default-branch push failures now feed the health audit —
cms-platform#279). These are the rest.

---

## B1 — The claude.ai account-store upload has no tracker

**The policy, stated by the owner:** when a centralized skill is added or
updated, until a laptop uploads it to the claude.ai account, an open issue
should be **created or updated** — and **nothing else should be blocked or
advertised as broken**.

**Current state, traced through actual exit paths (not inferred):**

- *"Blocks nothing"* — **already satisfied.** Nothing in `agentskills` CI reads
  `account-skills.txt`; `check_consistency.py`, `check_skills.py` and
  `generate_skills_lock.py --check` contain zero references to the account store.
  A skill declared but not uploaded leaves every job green. `skills-evals`'
  Tier-3 audit iterates only the *existing* manifest, so an un-uploaded skill is
  invisible to it and cannot fail it.
- *"Issue created or updated"* — **not automated.** Done by hand (issue #59,
  closed 2026-08-19). `sync_skills.py --verify` is the only tool that detects the
  state and it is laptop-only, prints to stdout, and calls no GitHub API. The one
  purpose-built automation — the propagation Routine's step 5 — is documented as
  `issue: unavailable` because the fired session has no GitHub access.

**Build the missing half, and only that half.** A dedicated marker issue that
opens/updates when `account-skills.txt` names a skill the account manifest lacks,
and closes when it does not. Model it on the existing pattern that already works
(`skills-evals#23`, opened/commented by `github-actions[bot]`) and on
`audit-scheduled-runs.js`'s marker-comment lifecycle — a hidden HTML marker to
find the one issue, comment on change, auto-close when clean.

**Constraints that are the whole point — violating them is worse than not
building it:**

- It must not gate, fail, or red anything. Not a required check, not a
  non-zero exit that blocks a merge.
- The detection needs the live account manifest, which only a laptop has. So
  either the laptop-side tool gains the issue call, or CI compares against
  something committed. **Do not invent a CI-side check that guesses upload state
  from repo contents** — it would be wrong in both directions.
- Adding a name to `account-skills.txt` is close to a one-way door (the upload
  API has no delete). Do not add names as a side effect of this work.

---

## B2 — `_agent-guidance` holds one adoption key, not two

Skill-bundle delivery is **double-keyed**: an allowlist entry in
`_agent-guidance`'s `repos.yml` **and** a `skills.lock` the repo committed
itself.

`_agent-guidance` has committed a `skills.lock` but is **not** in its own
`skills_bootstrap.repos` allowlist (which lists adamdaniel.ai,
agentskills-private, claude-memory-map, cms-platform, fastmail-actions,
GHA-bench, jodidaniel.com, repo-settings, scratch-claude-002, wsl-automation).

Determine which of the three states this is — mid-adoption, deliberate
self-exclusion (it excludes itself from its own AGENTS.md sync via
`SYNC_SELF_REPO`, so self-exclusion may well be intentional), or an oversight —
and either add the entry or **write down why it is excluded** so the next person
does not re-ask. A one-line comment in `repos.yml` costs nothing and ends the
question permanently.

Consequence either way: its lock is not re-pinned by the nightly bumper, so its
digests stay bare-hex. Harmless today (no gitleaks there), but it means the repo
is not exercising the mechanism it owns.

---

## B3 — Close out agentskills#87

Still open despite its own "ready to close" comment, and it should not close
until:

- The three bare-hex locks are relabelled. **cms-platform and GHA-bench should
  self-heal** — both are in the bootstrap allowlist and `--repin` re-derives
  every digest through the labelling generator. **Verify that actually happened**
  after the next nightly run rather than assuming; if the bumper skipped them,
  find out why.
- The downstream consequence is recorded (already commented on #87): labelling
  broke `skills-evals`' `plugin-marketplace` arm because the digest algorithm has
  **three independent copies** and only one moved. Fixed in skills-evals#36.

While you are there: the fixture builder `make_registry()` in
`test/test_propagation.py` writes lock digests in the **bare shape only**, which
is why the suite stayed green while production was red. That has been patched for
this case — check whether the same "fixture only exercises one shape" hazard
exists anywhere else in that suite.

---

## B4 — Prove the new push-lane audit actually fires (it still never has)

cms-platform#279/#280 extended the health audit to default-branch push failures,
and #282 then fixed a bug that stopped it running at all.

**Read this before assuming it works now.** A `dry_run` dispatch was made against
real data (run 32280743541) and reported `0 failing push run(s)`. That number was
FABRICATED: `${{ inputs.push_scan && '' || '--no-push-scan' }}` emitted the
opt-out unconditionally, so the push scan never executed and the count came from
a static default. The expression is fixed and linted now — but the consequence
stands: **the push lane has still never executed against real data even once.**
It has unit coverage and a negative control, and that is all.

Exercise it end to end on a real repo: cause (or find) a default-branch push
failure, confirm the audit opens or comments on the tracking issue within its
window, then confirm the clean-window auto-close works. An alerting mechanism
that has only ever been tested against stubs is not yet known to work.

Report the issue number it filed and the run that filed it.

---

## B5 — A workflow you cannot run on demand is one you cannot verify on demand

Hit for real this session: a fix to `skills-evals`' propagation arm merged, and
there was **no way to run `propagation.yml` and confirm it** —
`422 Workflow does not have 'workflow_dispatch' trigger`. Verification had to
wait for a push or the cron. That is a bad property for exactly the workflows
whose job is to detect drift.

A parsed audit (PyYAML, reading `d.get("on", d.get(True))` — a bare `on:` parses
as boolean `True`) across all 8 clones found **27 workflows with real triggers
and no `workflow_dispatch`**. They split cleanly, and the split is the whole
finding:

### Add it — a scheduled or push lane you cannot trigger by hand

| repo | workflow | current triggers |
|---|---|---|
| skills-evals | `propagation.yml` | pull_request, push, **schedule** |
| agentskills | `ci.yml` | pull_request, push, **schedule** |
| skills-evals | `ci.yml` | pull_request, push |
| `_agent-guidance` | `ci.yml` | pull_request, push |
| GHA-bench | `ci.yml` | pull_request, push |
| cms-platform | `self-ci.yml` | pull_request, push |

The two carrying a `schedule` are the priority: a scheduled probe you cannot
dispatch means every fix to it waits for the cron before anyone knows it worked.

`propagation.yml` deserves a `dry_run`-style input as well, mirroring
`scheduled-run-health.yml`'s, so a manual run can exercise the probe without
opening or updating its tracking issue.

### Do NOT add it — 20 `pull_request`-only workflows, several actively hazardous

`cms-editorial-workflow`, `e2e-tests`, `e2e-stub`, `parity-preview`,
`preview-media`, `visual-regression`, `platform-pin-consistency`,
`deploy-preview`, `dependabot-auto-merge`, `regression-review-reaper` (both
sites), plus `self-dependabot-auto-merge`.

Two reasons, and the second is the one that bites:

1. They need PR context that a dispatch has no way to supply.
2. **Several publish REQUIRED status contexts** — `editorial / validate-content`,
   `e2e / e2e`, `parity / parity`, `preview-media / preview-media`,
   `visual-regression / approve-regression`. Adding `workflow_dispatch` gives
   them *another event that can fire on the same head SHA*. That is precisely the
   input to the cancelled-required-check trap in SHARED-CONTEXT §6: where such a
   workflow also carries a `concurrency` group (`visual-regression.yml` does —
   `visual-regression-pr-<n>`, `cancel-in-progress: true`, and its context IS
   required), a duplicate dispatch can leave a cancelled run on the SHA and hard
   block the merge with `405 Required status check … is cancelled`.

So this is not "add `workflow_dispatch` everywhere". It is six specific
additions, and an explicit note in the others explaining why they do not get one
— otherwise a future tidy-up adds them and reintroduces a known incident.

**Verify each addition by actually dispatching it** and reporting the parsed
conclusion. An input you added but never fired is not known to work.
