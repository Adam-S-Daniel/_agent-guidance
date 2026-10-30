<!-- BEGIN MANAGED SECTION — DO NOT EDIT ABOVE "## Repo-specific additions" -->
<!-- Source: _agent-guidance -->
<!-- Sections: none -->
<!-- Mode: stub -->

# AGENTS.md

> **Managed by [`_agent-guidance`].**
> Edit only below the `## Repo-specific additions` header.
> Everything above it will be overwritten on the next sync.

## Fleet guidance is delivered once per session — not by this file

The account's full guidance — incidents, fleet policy, machine layout, the
traps that cost real outages — is installed into **user memory**
(`~/.claude/CLAUDE.md`) by the `fleet-memory` SessionStart hook, so it is
loaded **once per session** no matter how many repos are attached
([inlined per repo, it took 332.3k tokens](https://github.com/example-adopter/_agent-guidance/blob/main/docs/evidence/2026-08-29-guidance-inlined-in-every-repo.md)).

**Check the session-start verdict before you rely on it.** The hook prints one
line:

- `fleet-guidance: installed (v<id>, <n> bytes)` or `fleet-guidance: current` —
  the full guidance is in context. Use it.
- `fleet-guidance: DEGRADED — <reason>` — it is **not** in context. You have
  only what is below. Read `agents-md/base.md` in the `_agent-guidance`
  checkout (or on GitHub) before non-trivial work, and say in your reply that
  you were running degraded.
- `fleet-guidance: skipped (FLEET_GUIDANCE_SKIP set)` — also not in context,
  but by the machine owner's deliberate choice, not a fault. User memory is
  GLOBAL on a durable machine, so the guidance would otherwise load in every
  unrelated project on that box; `FLEET_GUIDANCE_SKIP` opts out and removes any
  block an earlier session installed. Read `agents-md/base.md` the same way you
  would when degraded — just don't report it as a problem or try to "fix" it.

No verdict at all means the hook never ran — treat that as DEGRADED.

## Codex reads the same block, from `~/.codex/AGENTS.md`

The hook writes the same block there whenever `~/.codex` exists. Setup for
local Codex and Codex Cloud, and how to verify delivery:
[`docs/codex-cloud.md`](https://github.com/example-adopter/_agent-guidance/blob/main/docs/codex-cloud.md).

## The floor: rules that hold even when the guidance did not load

These are the ones with teeth. They are restated here, deliberately, because a
session that lost the guidance must not also lose these.

- **Branch protection is real.** Fleet repos are PR-only on their default
  branch; a direct push is rejected (GH013), even from the repo's own
  workflows. Never design a bot that pushes to a protected default branch.
- **Every `uses:` is pinned through `.github/workflows/actions.lock`: an
  exact tag, locked, checked by a required `gh actions-lock --verify`
  failing on any pin finding.** A repo not yet migrated keeps the legacy
  form, a full 40-character commit SHA with no trailing version comment,
  until its migration PR; never mix the two in one repo. Cross-repo
  reusable workflows take a full SHA either way. The one carve-out is a ref
  into this account's own `example-site-platform`, which stays on its release tag.
- **Never commit secrets or `.env` files, and never print personal data to a
  CI log** — logs, artifacts and git history on a public repo are public.
- **A successful `git push` does not mean your commit exists.** A refused
  pre-commit hook still lets the push report success. Verify with
  `git merge-base --is-ancestor <sha> origin/<branch>` — it is the only check
  that names both the commit and the ref.
- **"The watch finished" is not "CI passed."** Read the parsed conclusions;
  never infer pass/fail from a watch command's exit code.
- **A GitHub 404 means "not authorized", not "not there."** Never report a
  repo, PR or branch as gone on a 404 alone.
- **The fleet spans TWO owners** — `example-adopter` and `example-adopter-two`. A query
  scoped to one returns a plausible, complete-shaped, wrong answer.
- **Anything you name gets its link** — what you hand over, what you are
  waiting on, and what you cite as already done.
- **Merge with a merge commit** (`gh pr merge --merge`), except in the three
  example-site-platform-managed repos (`example-site-platform`, `example-site-a`,
  `example-site-b`), which squash; do not amend published commits or
  force-push shared branches.
- **Keep this file under 32 KiB.** Codex truncates project instructions at
  that byte silently; the sync warns and the drift report flags
  `codex-truncated`.

<!-- END MANAGED SECTION -->
## Repo-specific additions

**`AGENTS.md` in this repo is a generated artifact.** Everything above the marker
is `scripts/build-agents-md.sh` output. By default (`AGENTS_MD_MODE=stub`, which
the header's `Mode: stub` line records) that is `agents-md/stub.md`: the pointer,
the session-start verdict and the floor rules. `base.md` is inlined only under
`AGENTS_MD_MODE=full`. Never edit this file's managed half; edit `stub.md` (or a
file under `agents-md/sections/`) and regenerate. CI regenerates and diffs it, so
a stub edit without a regenerated `AGENTS.md` fails the build.

**`agents-md/base.md` is the fleet-memory payload, with a mirror.** Every edit to
it is copied byte for byte to `.claude/hooks/fleet-guidance.md`, which this
repo's own sessions load. CI's "Fleet payload mirrors base.md" step
(`scripts/check-fleet-guidance-mirror.js`) fails on any difference and names the
fix. A PR that changes `base.md` shows the payload consumers receive on their
next session; one that changes `stub.md` shows the text each consumer repo's
`AGENTS.md` gets on the next sync.

Why it is committed here at all, when `sync.sh` writes it everywhere else: the
sync excludes its own repo (`SYNC_SELF_REPO`), so without this copy it would be
the one repo in the fleet whose agents never read the fleet's guidance.

Regenerate with:

```bash
printf '%s\n%s\n' "$(./scripts/build-agents-md.sh)" \
  "$(sed -n '/^## Repo-specific additions/,$p' AGENTS.md)" > AGENTS.md.new \
  && mv AGENTS.md.new AGENTS.md
```

**Split on the `## Repo-specific additions` heading LINE, never on the first
occurrence of that substring.** The managed block's own BEGIN header on line 1
quotes the marker verbatim (`DO NOT EDIT ABOVE "## Repo-specific additions"`),
so a substring split anchors on the header, keeps the whole prior managed block
as "repo-specific" content, and the next regen stacks a second managed block on
top of it. A doubled file regenerates to itself, so the staleness diff cannot
see it; `scripts/check-agents-md.sh` counts the markers and asserts their
order, and CI runs it ahead of the staleness check. (The 2026-08-19 incident:
[evidence](https://github.com/example-adopter/_agent-guidance/blob/main/docs/evidence/2026-08-19-agents-md-doubled-managed-block.md).)

**A new `##` heading in `agents-md/base.md` (or a new file under
`agents-md/sections/`) needs a row in `agents-md/eval-coverage.yml` in the
same PR.** Add it before the section lands, not after — CI's "Section manifest
covers every guidance heading" step (`scripts/check-guidance-coverage.js`)
fails otherwise, naming the heading and the two ways to close it: a `covered`
row pointing at a skills-evals fixture, or a `skipped` row giving a `reason`
and a `since` date. The same step also fails if a heading gets reworded
without updating the matching row's `heading` text (reported as a stale row,
with the nearest current heading offered as the likely rename target) or if a
row's `bytes` has drifted from the section's real size (fix with
`node scripts/check-guidance-coverage.js --write-bytes`).

**A PR that changes a `##` section's own extent — its body text, not just a
rename — needs an entry in [`docs/guidance-impact.md`](docs/guidance-impact.md)
in the same PR.** CI's "Guidance touch gate" step (`scripts/check-guidance-touch.js`)
fails otherwise, naming the section's `id` and the entry format. A pure
rename (the heading text changes, updated in `agents-md/eval-coverage.yml`,
body untouched) needs no entry; a body edit does, and a removed section
needs one typed `remove`. See that file's own header for the entry format
and what counts as a sufficient `Eval:` line.

**CI's one required context is `test`, and it is an aggregator.** `ci.yml`
runs the integration suite as `suite`, a matrix of four shards that each pass
their groups to `./test/run-tests.sh` through `TEST_GROUPS_ONLY`, and every
other gate in `checks`; the `test` job (id and name both `test`, the context
branch protection names) needs both, runs under `if: always()`, and fails
unless both concluded `success`. That is why neither `suite` nor `checks` is
required on its own: a red, cancelled or skipped shard or check already fails
`test`, and `if: always()` keeps `test` from being skipped, which would read
as passing. **Adding a test group means adding it to one shard** in `ci.yml`'s
`suite` matrix, in the same PR: the "CI shards cover every test group" step
(`scripts/check-ci-shards.js`) fails unless the shards' lists name every group
from `./test/run-tests.sh --list-groups` exactly once. Balance shards by the
per-group seconds in the suite's `Groups` table, not by count.

**Action pins.** This repo's own workflows name third-party actions by exact
release tag, and [`.github/workflows/actions.lock`](.github/workflows/actions.lock)
locks each tag to a commit ([ADR 0024](docs/decisions/0024-actions-pin-through-lockfiles.md)).
`agent-checks` runs `Adam-S-Daniel/verify-actions-lock` on every pull request.
Write the exact tag by hand, regenerate with `gh actions-lock --no-narrow`, and
commit both files. The suite's pin tests read the lockfile. The templates under
`templates/` keep full SHAs, because the sync delivers them to legacy repos.
