# Session B — make the remaining silent failures loud

Read `SHARED-CONTEXT.md` first. Use workflows and adversarial review. Pull
emergent issues into scope recursively. Merge what you land; cut a release and
confirm consumer bumps if you touch a cms-platform reusable (SHARED-CONTEXT §3).

**Corrected 2026-08-20 from
[`_agent-guidance#52`](https://github.com/Adam-S-Daniel/_agent-guidance/issues/52),
which re-measured most of the numbers below.** Four were wrong: §B2's corollary
about the nightly bumper, §B3's count of bare-hex locks, §B4's "it still never
has", and §B5's workflow counts *and* one entry on its ADD list. Each is
corrected in place and says what it used to say.

The theme: several things in this fleet fail without anyone finding out. One of
those gaps was closed (default-branch push failures now feed the health audit —
cms-platform#279, **now closed `completed`**). These are the rest — plus §B4a,
which is the discovery that closing #279 did not deliver its fix to seven of the
ten repos that need it.

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

~~Consequence either way: its lock is not re-pinned by the nightly bumper, so its
digests stay bare-hex.~~

**That consequence is FALSE, and it is the most-repeated wrong inference in this
pack.** `scripts/bump-consumer-locks.sh` **does not consult the
`skills_bootstrap.repos` allowlist at all** — it discovers its own targets and
deliberately KEEPS the self repo. The allowlist governs **HOOK DELIVERY only**.
`_agent-guidance`'s digests stayed bare for an entirely different reason (§B3).

Both halves of the question are now settled and written down at the source, so
this section is bookkeeping rather than work:

- **Why the absence is correct.** `repos.yml` already carries the explanation:
  both `sync.sh` and `drift-report.sh` drop `$SELF_REPO` before the per-repo
  loop, so an entry could not deliver anything or even be reported on. This repo
  SELF-HOSTS the hook, exactly as agentskills does. It is deliberate
  self-exclusion, not an oversight and not mid-adoption.
- **Why the corollary is called out there too.** The same comment ends with
  *"WHAT THIS ABSENCE DOES NOT MEAN: it says nothing about lock BUMPING … and a
  fresh reader re-derives it from this entry every time, so it is written down
  here."* A wrong inference that everyone reaches independently has to be
  refuted where it is reached, not only where it is disproved.

---

## B3 — Close out agentskills#87 — DONE, and the premise was wrong twice

**agentskills#87 is closed `completed` (2026-08-20T11:12:57Z, `mcp__github__`).**
Keep reading anyway: the two corrections below are the durable part, and both
were found by following this section's own instruction to *verify rather than
assume*.

> ~~Still open despite its own "ready to close" comment, and it should not close
> until: the **three** bare-hex locks are relabelled. **cms-platform and
> GHA-bench should self-heal** — both are in the bootstrap allowlist and
> `--repin` re-derives every digest through the labelling generator.~~

**Correction 1 — there were EIGHT, not three.** cms-platform, GHA-bench,
`_agent-guidance`, agentskills-private, claude-memory-map, fastmail-actions,
repo-settings and wsl-automation. agentskills#87 itself says "three" in one
comment and "the other 9" in another; **neither number is right**, so do not
take either from the issue.

**Correction 2 — the bumper does not, and CANNOT, self-heal any of them.** Not
"has not yet" — cannot. Proven two ways:

- **Mechanism.** `bump-consumer-locks.sh` gates on
  `generate_skills_lock.py --check-current`, which compares BUNDLE CONTENT at
  the pinned ref against the registry tree and **never reads the lock's stored
  values**. The generator's own `_label_digests` docstring says why labelling is
  applied at the document boundary: *"so every comparison BETWEEN builder
  outputs keeps working on bare hex. That is what leaves `--check-current`
  alone."* A shape defect is invisible to a content gate, by design.
- **Outcome.** The `adam` bundle has not moved since `94cdcc81`, so the gate
  returned **exit 0** on all eight and the script logged
  `bundle content unchanged — no re-pin needed`. `--repin` on the same file DOES
  relabel (8 bare → 8 labelled), so the writer was never the problem.

All eight were healed **by hand** on 2026-08-20 with every pin preserved — see
`README.md`, "The bare-hex locks". Had the bumper reached them first it would
have re-pinned **without `--ref`**, resolving `HEAD` and advancing all eight
pins: a fleet-wide content advance wearing a shape repair's PR body. That is now
guarded (`--ref` on the format branch; see the script's *"A SHAPE repair is
never a CONTENT advance"*), and the same missing-`--ref` defect in the
human-facing remediation line is agentskills#106 (`SHARED-CONTEXT.md` §7).

**One more coupling worth knowing before you touch either script.**
`bump-consumer-locks.sh` greps `^FAILED:` out of the generator to decide whether
to WRITE. That prefix is a cross-repo API, and when this was found it covered
three conditions while the caller's comment claimed one. Fixed at the source
since (verified 2026-08-20): `FAILED:` now means the empty-`skills` map only,
and missing / non-map answer `ERROR:`.
- The downstream consequence is recorded (already commented on #87): labelling
  broke `skills-evals`' `plugin-marketplace` arm because the digest algorithm has
  **three independent copies** and only one moved. Fixed in skills-evals#36.

While you are there: the fixture builder `make_registry()` in
`test/test_propagation.py` writes lock digests in the **bare shape only**, which
is why the suite stayed green while production was red. That has been patched for
this case — check whether the same "fixture only exercises one shape" hazard
exists anywhere else in that suite.

---

## B4 — Prove the new push-lane audit actually fires — DONE, and it found a bug

cms-platform#279/#280 extended the health audit to default-branch push failures,
and #282 then fixed a bug that stopped it running at all.

**Read this before assuming it works now.** A `dry_run` dispatch was made against
real data (run 32280743541) and reported `0 failing push run(s)`. That number was
FABRICATED: `${{ inputs.push_scan && '' || '--no-push-scan' }}` emitted the
opt-out unconditionally, so the push scan never executed and the count came from
a static default. The expression is fixed and linted now — but the consequence
stands: **the push lane has still never executed against real data even once.**
It has unit coverage and a negative control, and that is all.

~~Exercise it end to end on a real repo … An alerting mechanism that has only
ever been tested against stubs is not yet known to work.~~

**Done, 2026-08-20 — and the exercise paid for itself immediately.** Run
**32320712148** (`workflow_dispatch`, non-dry, conclusion `success`) commented on
adamdaniel.ai#3173. The counts were cross-checked against the API independently
*before* the run was trusted: 3 failing scheduled + 8 failing default-branch push
runs in the 48h window — exact match.

**Its first output on real data exposed a shipped bug.** The push section
rendered "**secrets-scan.yml** — 8 failing **scheduled** run(s)" under a heading
saying **push**. `renderFindings()` hardcoded the scheduled-lane noun and serves
both lanes; the existing test asserted the section **HEADING** and never the line
beneath it. Fixed, with a test that asserts the line beneath, per lane.

That is the general lesson, and it is the same one as `SHARED-CONTEXT.md` §6b:
**a test that asserts the container does not test the contents.** The bug was
reachable only on real data because the stub fixtures never populated both lanes
at once — and the alerting mechanism this section exists to validate would have
shipped mislabelling every finding it ever produced.

---

## B4a — #279's fix reached none of the fleet callers — CLOSED by a hand bump; the structural half is still open

**Not in the original pack at all; added from #52 — and the first version of
this section was born stale, which is itself the lesson.** It asserted in the
present tense that seven repos pin `scheduled-run-health.yml@v0.1.85`. They had
all been bumped to `v0.1.87` **roughly fifteen hours before that text was
written**, including `_agent-guidance`, the repo the pack lives in — and the
tracking issue #52 points at, cms-platform#283, had announced that very bump in
its own body (*"I am bumping all seven by hand to `v0.1.87` once it is cut"*).
The gap was real; it was resolved before it was documented, by the mitigation
the cited issue had already published. Read the rest as history plus one live
remainder.

**The gap, as it stood.** Closing cms-platform#279 was not the same as
delivering it. Only the two cms-platform site consumers were on `v0.1.86`+; the
other seven pinned `@v0.1.85`, **which has no push lane at all** (`git show
v0.1.85:.github/workflows/scheduled-run-health.yml | grep push_scan` → no
matches; at `v0.1.87` the same file carries `push_scan: { type: boolean, default:
true }`). **skills-evals had 14 failing default-branch push runs and structurally
could not see any of them.**

**Closed 2026-08-20T05:34Z, by hand, in all seven.** Each carries a commit
titled *"Bump the health audit to v0.1.87, so this repo's silent push failures
get seen"* — agentskills `a029fc0`, repo-settings `b1d75b2`, `_agent-guidance`
`317de9c`, claude-memory-map `4410a46`, fastmail-actions `29ed262`, GHA-bench
`6ef5628d`, skills-evals `d0edccf`. Measured on each clone's `origin/main`:
`uses:@v0.1.87` **and** `platform_ref: v0.1.87`, the two refs in agreement in
all seven. The push lane defaults on, so nothing else had to change.

**They are still byte-uniform, which is the durable observation.** All seven
callers resolve to ONE blob — `git rev-parse origin/main:.github/workflows/scheduled-run-health.yml`
returns `0d947f9d` in every one. That was true at `v0.1.85` and is true at
`v0.1.87`. Two things follow, and the second is why this section still exists:

- **A bump is mechanical** — two refs per repo, no permission or input change.
  Which is exactly why it was doable by hand, once.
- **Their green was never coverage.** Every one of their latest runs was
  `success`, and at `v0.1.85` that meant only "the schedule lane found nothing" —
  precisely the shape of silence this session is about. A green audit is evidence
  about the lane that ran, never about the lane that was not compiled in.

### What is still open: nothing bumps these callers automatically

The hand bump was a one-off, and the drift has **already restarted**. Measured
2026-08-20: the two site consumers are on `@v0.1.88` (moved by `platform-bump`
with the release) while all seven fleet callers sit at `@v0.1.87`. That is the
same gap one release later, opened by the same absence.

The pin is unmaintainable in **both** directions, which is why this is an issue
and not a chore. Measured across the seven on 2026-08-20: three carry
`ignore: Adam-S-Daniel/cms-platform/*` (`_agent-guidance`, fastmail-actions,
skills-evals) — correct for the two site consumers *because `platform-bump` owns
the version atomically*, but `platform-bump` never targets these repos, so
nothing bumps them at all. The other four lack it (repo-settings,
claude-memory-map, GHA-bench have a `dependabot.yml` without the entry;
agentskills has no `dependabot.yml`) and would take a **half bump**: `uses:@`
moves (a dependency ref), `platform_ref:` does not (a `with:` input value). The
result is a new workflow driving an old script, and
`flag() { return process.argv.includes(...) }` silently ignores a flag it does
not know — **green run, zero detection, no error anywhere.**

Tracked as **cms-platform#283**, which is **open as measured 2026-08-20 via
`mcp__github__`**, with PR **#296** ("Two anti-silent-green lints: consumer nudge
contexts (#284) and pin agreement (#283)") open against it — so check both before
acting. #283's own body predicted this exactly: *"That is a one-off and does not
close this issue — the next release re-opens the same gap."*

One caveat if you quote #283: its prose says *"Ten repos call
`scheduled-run-health.yml`"* while the table directly beneath it enumerates
**nine** (the seven above plus adamdaniel.ai and jodidaniel.com). Nine is what
this session could enumerate by reading each clone's `origin/main`; GitHub code
search returns fewer still and is not authoritative here — it silently omits the
private and less-recently-indexed callers (repo-settings, claude-memory-map,
fastmail-actions, jodidaniel.com were all missing from it while present on disk).
Enumerate from clones, not from the search index.

---

## B5 — A workflow you cannot run on demand is one you cannot verify on demand

Hit for real this session: a fix to `skills-evals`' propagation arm merged, and
there was **no way to run `propagation.yml` and confirm it** —
`422 Workflow does not have 'workflow_dispatch' trigger`. Verification had to
wait for a push or the cron. That is a bad property for exactly the workflows
whose job is to detect drift.

A parsed audit (PyYAML, reading `d.get("on", d.get(True))` — a bare `on:` parses
as boolean `True`) found the workflows with real triggers and no
`workflow_dispatch`. They split cleanly, and the split is the whole finding.

**The counts below were wrong in two independent ways — corrected from #52, and
re-check them before quoting either.** The paragraph used to say *"across all 8
clones found **27** workflows"*:

- **It is 30, not 27, over 13 clones, not 8.** The extra three are in
  **claude-memory-map** and **repo-settings**, which appear on neither list
  below — so they were not merely miscounted, they were unexamined. Enumerate
  them before acting on this section.
- **The arithmetic never closed either.** The DO-NOT list as actually enumerated
  is **21** items, not the 20 it claims, so "6 + 20 = 27" fails against both its
  own list and the real total. A count that does not add up against its own
  enumeration is a stop-and-report, not a rounding difference
  (`SHARED-CONTEXT.md` §7) — and here it was the symptom of two whole clones
  being outside the sweep.

### Add it — SHIPPED, all five; only the `self-ci.yml` verdict is still guidance

**All five rows landed on 2026-08-20, before this section was written to
describe them as outstanding.** They are kept, marked, so the next session does
not re-do finished work — and so the reason each one was worth doing survives.
`workflow_dispatch` presence re-derived per row from each clone's `origin/main`;
introducing commit found with `git log -S'workflow_dispatch' -- <file>`.

| repo | workflow | triggers when listed | status |
|---|---|---|---|
| skills-evals | `propagation.yml` | pull_request, push, **schedule** | **SHIPPED** `6c92c91` 2026-08-20T01:59:52Z |
| agentskills | `ci.yml` | pull_request, push, **schedule** | **SHIPPED** `3f40330` 2026-08-20T02:01:30Z |
| skills-evals | `ci.yml` | pull_request, push | **SHIPPED** `6c92c91` 2026-08-20T01:59:52Z |
| `_agent-guidance` | `ci.yml` | pull_request, push | **SHIPPED** `442af81` 2026-08-20T05:29:51Z |
| GHA-bench | `ci.yml` | pull_request, push | **SHIPPED** `74ae650f` 2026-08-20T01:59:18Z |
| ~~cms-platform~~ | ~~`self-ci.yml`~~ | — | **REMOVED from this list — see below** |

~~The two carrying a `schedule` are the priority: a scheduled probe you cannot
dispatch means every fix to it waits for the cron before anyone knows it
worked.~~ ~~`propagation.yml` deserves a `dry_run`-style input as well,
mirroring `scheduled-run-health.yml`'s, so a manual run can exercise the probe
without opening or updating its tracking issue.~~ **Both done.**
`propagation.yml` carries `dry_run: { type: boolean, default: true }` — a real
`boolean`, deliberately NOT the `string` + `fromJSON` shape
`scheduled-run-health.yml` needs (that workaround exists only because a typed
boolean crosses a `workflow_call` boundary as a string; nothing here does, and
its own comment says not to "fix" it in either direction). The default is
**true**, inverted from the schedule's, so a verification dispatch exercises the
probe and the dedupe lookup and stops one line short of the write.

**`cms-platform/self-ci.yml` was on this ADD list and must not go back on it —
but the stated reason has expired, and that matters more than the verdict.** The
reason given was that the file *"publishes all four of that repo's required
status contexts and carries `cancel-in-progress: true`"*. The first half holds:
`actionlint`, `ruby-theme-specs`, `node-unit-lints` and `plugin-validate` are
this repo's four required contexts, per cms-platform's own
`repo-settings.yml` (`ruleset_library.platform-main` → `required_status_checks`). **The second half is no longer true and was
already false when it was written** — cms-platform#285 removed the group, and
`grep -c '^concurrency:' self-ci.yml` on `origin/main` returns **0**. The file
says so itself, at lines 67–74: *"NO `concurrency:` BLOCK — a deliberate,
load-bearing ABSENCE. Do not add one… this workflow carried
`group: self-ci-<event>-<pr|ref>` with `cancel-in-progress: true` until
cms-platform#285."* Two paragraphs below, this section's own table already said
both surviving groups had been removed at `v0.1.87` — so the section contradicted
itself, and this is the correction.

**The verdict survives the correction because it never rested on the group.**
Adding `workflow_dispatch` to a workflow publishing four required contexts gives
those contexts another event that can fire on the same head SHA — the input to
the cancelled-required-check trap, which point 2 below spells out **does not
depend on a `concurrency` group existing**. It would have been the documented
incident at the time, when the group was there; the hazard is now the
narrower-but-real one, and `v0.1.88`'s widening (a `timeout-minutes` wall also
reports `cancelled`) is the reason not to treat a groupless workflow as safe by
default. `self-ci.yml` belongs on the DO-NOT list either way, and it remains the
clearest demonstration that the two lists were assembled by trigger shape rather
than by asking what each workflow publishes.

~~Verify each addition by actually dispatching it and reporting the parsed
conclusion.~~ Still the right rule for the **next** addition. For these five the
instruction is spent: they are on `main`, so what remains is not a dispatch to
perform but a claim to check — read the current `on:` block before assuming
either way. An input you add but never fire is not known to work.


### Do NOT add it — the `pull_request`-only workflows, several actively hazardous

(Enumerated as **21**, not the "20" this heading used to claim; and
`cms-platform/self-ci.yml` belongs here too, per the note above.)

`cms-editorial-workflow`, `e2e-tests`, `e2e-stub`, `parity-preview`,
`preview-media`, `visual-regression`, `platform-pin-consistency`,
`deploy-preview`, `dependabot-auto-merge`, `regression-review-reaper` (both
sites), plus `self-dependabot-auto-merge`.

**These are WORKFLOW names, not context names** — correct as written here, and
worth flagging because the same strings are wrong the moment you use one to
search a ruleset. `platform-pin-consistency.yml` publishes
`pin-consistency / pin-consistency`; see `A-trigger-narrowing.md`.

Two reasons, and the second is the one that bites:

1. They need PR context that a dispatch has no way to supply.
2. **Several publish REQUIRED status contexts** — `editorial / validate-content`,
   `e2e / e2e`, `parity / parity`, `preview-media / preview-media`,
   `visual-regression / approve-regression`. Adding `workflow_dispatch` gives
   them *another event that can fire on the same head SHA*. That is precisely the
   input to the cancelled-required-check trap in SHARED-CONTEXT §6, and it is
   reason enough on its own — **it does not depend on a `concurrency` group
   existing**, which is where the original framing of this point was too narrow.

   **What was measured, and what has since shipped.** At `v0.1.86` exactly TWO
   required-context publishers carried a group, and **the group was declared on
   the REUSABLE** — so grepping a consumer's caller finds nothing and the hazard
   reads as absent:

   | reusable | group key | `cancel-in-progress` | required context |
   |---|---|---|---|
   | `secrets-scan.yml` | `…-${{ github.event_name }}-${{ pr.number \|\| github.ref }}` | true | `scan / scan` |
   | `visual-regression.yml` | `visual-regression-pr-${{ pr.number }}` — **no event, no sha** | true | `visual-regression / approve-regression` |
   | `cms-editorial-workflow.yml` | job-level, `auto-merge-when-ready` only | false | `editorial / validate-content` — **not** in a group |
   | `e2e-required-stub`, `e2e-tests`, `parity-preview`, `preview-media` | none | — | safe |

   **SUPERSEDED as of cms-platform `v0.1.87`:** both groups were **removed**
   (cms-platform#285, closed `completed` 2026-08-20), each leaving a tombstone
   comment. Verified on cms-platform `origin/main`: *"NO `concurrency:` BLOCK —
   a deliberate, load-bearing ABSENCE. Do not add one."* `secrets-scan.yml`'s
   went too, which is stricter than #285 proposed and costs nothing the issue
   was protecting.

   Do not re-derive the narrowing that was tried and rejected: dropping
   `reopened` from a caller's `pull_request.types` cannot close
   `visual-regression`, because the observed collision was an `opened` +
   `synchronize` burst and both are required for the check to exist at all
   (`A-trigger-narrowing.md`, DO-NOT #4). And `v0.1.88` then widened the
   invariant again after `timeout-minutes` produced `cancelled` conclusions with
   no group in sight — `SHARED-CONTEXT.md` §6 carries the current form.

So this was never "add `workflow_dispatch` everywhere". It was a small, specific
set — **five**, all now shipped, once `self-ci.yml` came off the list — plus an
explicit note in the others explaining why they do not get one. That note is the
part still owed: without it a future tidy-up adds them and reintroduces a known
incident, and `self-ci.yml`'s appearance on the ADD list is exactly what that
tidy-up looks like when it happens to the pack itself.

**Before adding a sixth, re-derive the sweep against the real 30 over 13
clones** — the audit that produced these lists missed two whole repos, so a
workflow absent from both lists has not been judged, only overlooked.
