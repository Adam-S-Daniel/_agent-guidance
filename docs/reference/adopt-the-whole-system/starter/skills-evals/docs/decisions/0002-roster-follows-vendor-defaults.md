# ADR 0002: The roster seats each tier's vendor-default model at once and retires the version it superseded after a buffer

> Note: ADR 0002 is also used by [0002-runs-bill-the-api-org-not-the-subscription.md](0002-runs-bill-the-api-org-not-the-subscription.md) (real runs bill the API organization); the two are distinct decisions, numbered in parallel by mistake.

- **Status:** accepted (2026-09-27); decision 1 revised 2026-09-28 (see the
  second update below)
- **Issue:** [#202](https://github.com/Adam-S-Daniel/skills-evals/issues/202);
  revision in [#203](https://github.com/Adam-S-Daniel/skills-evals/pull/203)
- **Deciders:** the owner, 2026-09-27 and 2026-09-28
- **Amended 2026-10-25:** the superseded-model buffer
  (`superseded_exit_weeks`) is now 0 and the arm enter/exit windows are 2/2
  weeks (were 1, 4 and 8), the owner's decision: a roster seating a replaced
  model is "entirely backward-looking". The text below records the original
  decision.

## Context

Since 2026-09-22 the roster seated two kinds of arm: every model at or above
the 10% entry bar of rankable census usage, and the newest model past the
7-day cooling-off in a tier that some model already qualified by usage. Both
read the fleet's usage of individual VERSIONS.

Opus 5.5 shipped on 2026-09-22 and became the `opus` alias's default in
Claude Code. The roster step of
[run 35812851203](https://github.com/Adam-S-Daniel/skills-evals/actions/runs/35812851203)
excluded it as inside the cooling-off, so the earliest it could be seated was
about 2026-09-29, and paid eval runs were put on hold until then (HANDOFF.md,
"Eval runs on hold"). Meanwhile `claude-opus-5` kept a usage seat on 45.2%
of the 2026-09-22 census that the fleet will move off as soon as the alias
does.

Evals are valued going forward only. What the fleet will run next week is the
vendor's default version of each family it uses, not the version its history
happened to accumulate usage on.

## Decision

1. **Read the vendor defaults from the freshly installed CLI, with no
   credential.** CI installs the npm latest Claude Code on every run (never a
   CLI already on the runner). `scripts/probe_model_defaults.py` then starts,
   for each family word on the policy's tier ladder,
   `claude -p --model <alias> --output-format stream-json --verbose` in a
   scrubbed environment — PATH, a fresh temporary HOME with its XDG
   directories, LANG=C, `DISABLE_AUTOUPDATER=1` and
   `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, stdin from `/dev/null`, and
   nothing else — and reads the `model` of the first
   `{"type":"system","subtype":"init"}` event. The CLI resolves the alias from
   a table built into its binary, so resolution does not depend on the
   network (with networking removed the ids are identical). It does open
   unauthenticated TLS connections of its own before and after init
   (measured with strace, #203 probe round 1); no credential exists in the
   probe's environment, so nothing can be billed or leaked. The process group
   is terminated as soon as the event is read (SIGTERM, then SIGKILL after
   5 s, then SIGKILL to the whole group regardless), with a 60 s bound per
   alias. Measured 2026-09-28 on 2.1.283 with an empty
   environment: `opus`, `sonnet`, `haiku` and `fable` each resolved to a model
   id, `apiKeySource` was `none`, and `mythos` was echoed back unchanged — a
   word the CLI echoes back is not an alias and is recorded as skipped. The
   document records the probe time, the CLI's sanitised version line, the
   `{alias: id}` defaults, the skipped aliases, and a class per failed alias
   (`no-init`, `timeout`, `bad-model`, or an exception class). `harness/
   roster.py --defaults` takes an id when it is one of this run's available
   models — a dated snapshot the catalogue collapses onto its undated alias
   stands for that alias, and so does a dated `<base>-YYYYMMDD` the catalogue
   does not list while it lists `<base>` — AND the family word in it is the
   alias. Anything else, and every alias the probe failed on, leaves that tier
   unresolved, with a `roster: ` warning; decision 6 says which of those
   freeze.
2. **New default → seat now.** In a tier whose default resolved, the default
   is an arm whenever the tier is on the roster — a model in it clears the
   entry bar, ANOTHER previous arm in it is itself still seated this run
   (e.g. a superseded arm inside its buffer), or the census is unusable (the
   existing all-tiers fallback). **No cooling-off.** Its reason names the
   CLI version and probe time and why the tier is on the roster. The
   default's OWN previous seat never puts its tier on the roster: when none
   of those holds and the default is itself a previous arm, it gets the same
   exit check as any previous arm — held on a stale or too-thin census, held
   while at or above `arm_exit_usage_pct` over `arm_exit_window_weeks`, and
   otherwise retired with that evidence (#203 round 1; before it, a seated
   default could never retire). That exit check reads the TIER's combined
   share — every model of the default's family, summed over the same
   denominator — not the default's own (#203 round 2): measured on its own, a
   tier steady at 5% lost its only arm the week after the fleet moved to the
   new default. A retirement's evidence reports the tier share and says so.
3. **Usage picks tiers, not versions.** Inside such a tier a superseded
   model earns no seat from its usage however large; its usage still counts
   toward qualifying the tier. A model newer than the default (a release the
   vendor has not made the default) earns no NEW seat; one that is already a
   previous arm gets the normal exit check rather than an immediate
   retirement, so a default that lags a release by a run cannot seat it one
   week and drop it, still heavily used, the next (#203 round 1).
4. **Superseded → retire after a buffer.** A previous arm superseded by its
   tier's default keeps its seat until `superseded_exit_weeks` (new policy
   key, **1**) complete ISO weeks — Monday 00:00 UTC to Monday, beginning at
   or after the default's `created_at` and ended by the census's
   `generated_at` — have passed, **and** its share over the most recent such
   weeks is under `arm_exit_usage_pct` (2%). A stale census, or a buffer
   window under either ranked-usage floor, holds it. `0` retires it on the
   first run that sees it superseded. The 8-week exit window does not apply
   to a superseded arm.
5. **A default governs its own family (#203 round 2).** A rung can hold peer
   families (`[fable, mythos]`, a different access programme). The vendor-
   default rules apply only to models whose family word — the ladder word in
   the id — is the resolved alias; a model of another peer family is decided
   by rules 1 to 3 as before, neither superseded by nor "newer than" another
   family's default, and its usage does not put that default's tier on the
   roster. So two peer aliases naming models of their own families both
   resolve; an alias naming another family's model is unresolved on its own;
   the one remaining conflict is two spellings of the same family word naming
   different models.
6. **A failed probe, or a catalogue mismatch, freezes its family for that
   run (#203 probe round 1; narrowed in probe rounds 2, 3 and 5; folded into
   one freeze in round 7, R7-1).** A PROBE FAILURE freezes: the probe
   recorded an error class for the alias, it answered with a model of the
   wrong tier or family (`wrong-tier`, `wrong-family` — the CLI said
   something nonsensical about its own alias, so it is treated as a failure,
   probe round 3), it answered with a model that has no `created_at` to
   start a predecessor's buffer from (`no-created-at` — round 5, R5-1: this
   is a probe failure, not a catalogue mismatch, so the family freezes with
   the same wording as any other probe failure), or the document is
   unreadable, junk, answered for no ladder alias, or is eval.yml's
   stand-in for a probe script that exited non-zero (`probe-exited`, "the
   probe script exited with an error") — then every family on the ladder
   except the aliases the probe `skipped`. A `{}` from a probe that ran and
   resolved nothing stays `no-defaults`. A CATALOGUE MISMATCH — the probe
   answered, but the id is not an available model this run (`not-available`,
   `ambiguous-snapshot` — an undated id resolves to its one dated
   `<id>-YYYYMMDD` when the catalogue lists only that, and two or more are
   ambiguous) — FREEZES its family too, through the identical rule below,
   never through rules 1 to 4: only the wording says a mismatch rather than
   a failure, in reasons, `defaults_mismatched` (`{alias: {id, class}}`)
   rather than `defaults_failed`, and the warning and summary, so a human
   can tell the two apart. This is the GOVERNING GUARANTEE (first stated as
   the owner's decision in round 5, R5-2; made structural in round 7,
   R7-1): a single run whose probe answer is a failure or a catalogue
   mismatch changes no seat that a clean run would not change. Rounds 4
   through 7 each tried a narrower carve-out instead — deciding the
   mismatched family on a guessed "effective default" (its newest listed
   previous arm, or a usage-qualified model with none listed) — and each
   time a different branch of that guess retired or excluded a seat a clean
   week keeps; round 7 replaced the guess with the identical freeze a probe
   failure gets, closing every such branch at once. Where the guarantee
   conflicts with "a persistent mismatch still seats the model the fleet
   uses" — the model actually used has moved past every listed arm, but the
   probe cannot corroborate it — the guarantee wins: the listed seat holds,
   loudly (the mismatch's warning, summary line and open tracking issue),
   and a human fixes the probe. In a frozen family every previous arm keeps
   its seat ("vendor default for `<alias>` unknown this run (probe:
   <class>); held; none retired on a failed probe", or, on a mismatch,
   "the CLI's default `<id>` for `<alias>` does not match this run's
   catalogue (<class>); held; none retired on a catalogue mismatch") unless
   it has left the Models API, and nothing else is retired — held under its
   DATED id too (R5-3, round 5, and, for a SEATED vendor default, round 7's
   R7-2: `_default_rung_decision`'s `model_id == default_id` branch had
   missed the same alias collapse the superseded-arm buffer already used).
   While the family still holds a seat the Models API lists, it gets NO new
   seat at all (probe round 3): a usage seat granted there was one the next
   clean week retired. Only a family that would otherwise vanish from the
   roster — no held arm left in the API — is seated: by the usage entry bar
   (rule 1, probe round 2), with the freeze named in its reason, or, with no
   usable enter window — a missing or stale census, or a fresh one whose
   enter window is under the ranked-usage floors — as the newest in its
   tier, exactly as the no-probe fallback would seat it (probe round 3).
   This is why a dated snapshot replaced by a DIFFERENT dated snapshot of
   the same base — the base itself never listed — is a different model,
   not a rename: the old snapshot retires as no longer returned by the
   Models API rather than being held under the new one, and a freeze that
   would otherwise leave the family with no held arm left in the API may
   then seat the new snapshot by usage, as a family with no listed seat
   (#203 adversarial round 10, S1).
   Without that, a CLI that never initialises unauthenticated emptied the
   roster once the old models left the API. Such a family with a usable
   enter window and no model clearing the entry bar (its default at 0%, for
   instance) gets no seat: the same outcome a clean run gives, since a tier
   no model of which clears the entry bar is not on the roster. A usage seat
   granted during a freeze because the family would otherwise vanish can
   outlast the freeze: once a clean probe names a newer default, that seat
   is a superseded previous arm — the same as any used arm — and leaves
   only through the `superseded_exit_weeks` buffer; it is not retired just
   because the freeze ended. The earlier rule — fall back to both usage
   rules — flipped seats on a one-week failure: a seated default retired,
   or a preview seated, and the next clean probe undid it. The freeze is
   loud: `defaults_failed` or `defaults_mismatched` in the published
   roster, a line at the top of the step summary, and its own fixed
   `::warning::` from the proposal step for each class present (#203 probe
   round 8, R8-1 — the step had read only `defaults_failed`), which keeps
   the tracking issue open even when the proposal is "same"; the issue's
   title and first line say "probe failed" only for a genuine failure, a
   mismatch's own wording, or both when both classes are present in the
   same run, so a mismatch-only run never claims the probe failed; its eval
   sentence says whether the eval step
   succeeded, failed or did not run (it runs before the step that publishes
   to `eval-results`, so a successful eval's sentence says that step
   publishes next). It is **per run, never carried** (the owner's
   decision): the next run's probe decides afresh. The probe is not the eval
   — the eval authenticates and the probe must not — so a probe failure does
   not stop the eval. The committed `evals/roster.yml` keeps no
   `defaults` block; the published roster keeps one for the reviewer (source
   `claude-code-cli <version>`, `probed_at`, resolved, unresolved).
7. **Unchanged:** a family with no default in the document (never probed,
   or `skipped`) keeps both usage rules verbatim, cooling-off included; the
   preflight pick keeps its cooling-off; the judge rule is unchanged. With no
   defaults document at all the roster is the same one the pre-#202 code
   computes, with one exception (#203 adversarial round 10, N1): a previous
   arm whose listed spelling has switched between dated and undated since
   the previous run is still recognised as that arm — `_seated_form` is not
   gated on a defaults document existing — rather than reading as no longer
   returned by the Models API and dropping its seat outright.

## Consequences

- **The 2026-09-22 census proposes `claude-sonnet-5`, `claude-opus-5` and
  `claude-opus-5-5` at once**, and `claude-opus-5` retires on the first run
  whose census covers a complete ISO week (2026-W40 or later) with it under
  2%. The eval hold no longer waits on the cooling-off.
- **Usage now only picks tiers.** A tier's version churn no longer re-weighs
  the arm set; a fleet that keeps using an old version after its default
  moves on is no longer measured on it once the buffer has run.
- **A broken CLI degrades loudly.** A probe that cannot read an init event,
  times out, or reads a model that is not an id records only that class,
  never the CLI's output; the roster freezes that family for the run and
  says so in the summary, a `::warning::` and the tracking issue. The
  proposal step that carries the warning and the issue runs unless the
  workflow is cancelled, so it fires even when the eval step before it
  failed, and a failed `gh issue` write there is a fixed `::warning::`,
  never a failed job — but a failed `git push` of `roster/proposal` still
  fails it, because the proposal branch must exist for review. A failed
  probe fails no workflow step. A wrong
  default is bounded by the catalogue check: an id that is not an available
  model this run freezes the family exactly like a probe failure (#203
  probe round 7); an id of the wrong tier or family freezes it too, always
  has.
- **A tier between the bars keeps an arm across a version change**, because
  the seated default's exit check reads the tier's share; a tier the fleet
  genuinely leaves still retires it by the exit bar.
- **Peer families are independent.** Documenting a `fable` default neither
  unseats a heavily used `mythos` model nor changes the judge.
- **The published roster records what it read** (`defaults`: the CLI
  version, probe time, `{alias: id}`, and unresolved aliases with reasons), so
  a reviewer of a proposal can see why a seat appeared.
- **One CLI start per alias in the roster step, with no credential.** It runs
  before the Models API bearer is exported, and the probe scrubs the CLI's
  environment itself, so the bearer could not reach it even if the order
  changed.
- **The defaults are the installed CLI's**, and that is now the point: CI
  installs the npm latest every run, so the probe reports what the fleet's
  up-to-date CLIs will resolve the alias to.
- **Update 2026-09-27 (the owner's decision, #202):** the model cooling-off
  is set to 0 (`cooling_off_days: 0`), so the tiers still on rules 1 and 2
  (e.g. haiku) and the preflight pick take the newest model at once; the knob
  and its machinery are kept. The harness is unpinned as well: CI uses the
  Claude Code already on the runner, else installs the latest, and records
  the version per run (step summary; `harness.version`, `models_used` and
  `judge_models_used` in every arm's `summary.json`) instead of in a pin.
- **Update 2026-09-28 (the owner's decisions, #203):** "always ensure latest"
  — CI installs the npm latest Claude Code on every run instead of reusing one
  on the runner, and fails if another `claude` on PATH shadows it. With the
  installed CLI always current, decision 1 moved from the docs page to an
  unauthenticated probe of that CLI, and the carried-defaults machinery that
  the docs page had needed — the committed roster's `defaults:` block, its
  expiry `defaults_carry_max_age_days`, staleness drops and the loud
  `carried` signals (the former decisions 4a and 4b), and the display-name
  match — was removed. It
  existed to bridge a docs outage. A probe failure now freezes the families
  it could not establish for that run only (decision 6, #203 probe round 1),
  and a freeze still seats a model that clears the usage entry bar (#203
  probe round 2); nothing is carried across runs.
- **Update 2026-09-28 (#203 probe round 7, R7-1 and R7-2):** a catalogue
  mismatch (`not-available`, `ambiguous-snapshot`) now freezes its family
  through the identical rule a probe failure gets, rather than deciding it
  on a guessed "effective default" — rounds 4 through 7 each found a
  different branch of that guess retiring or excluding a seat a clean week
  keeps, so the guess is gone and its machinery
  (`effective_by_family`/`effective_words`) with it; only the wording
  (`defaults_mismatched`, `mismatch_words`) still tells a mismatch from a
  failure. Fixed in the same round: a seated vendor default held only under
  its DATED id (`_default_rung_decision`'s `model_id == default_id` branch)
  had missed the alias collapse the superseded-arm buffer already used, so
  it wrongly read as excluded from the roster on a clean run.

## Alternatives considered

- **Read the defaults off the Claude Code model-config docs page**
  (decision 1 from 2026-09-27 to 2026-09-28). Dropped. The page names
  display names ("Opus 5.5"), not ids, so it needed a match through the
  Models API's `display_name`, and a docs outage needed a whole
  carry-and-expiry mechanism (a committed `defaults:` block, a 14-day expiry,
  staleness evidence, loud warnings and an issue kept open) to avoid flipping
  every tier to the usage rules for a run. The probe gives ids directly, and
  its failure mode needs no bridge.
- **Probe a PINNED CLI.** Rejected. The CLI resolves aliases itself, so a
  pinned one reports the defaults of its own release and lags every change
  of default. Measured 2026-09-27 with an invalid key: CLI 2.1.283 resolves
  `opus` to the newer model, while 2.1.211, CI's pin at the time, resolved
  `opus` and `fable` to the previous generation. Installing the npm latest on
  every run is what makes the probe authoritative.
- **Shorten the cooling-off.** Rejected: it would still seat by age rather
  than by what the vendor defaults to, and would also seat a preview the
  vendor has not made the default.
