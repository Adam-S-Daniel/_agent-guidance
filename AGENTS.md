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
([2026-08-29: inlined per repo, it took 332.3k tokens](https://github.com/Adam-S-Daniel/_agent-guidance/blob/main/docs/evidence/2026-08-29-guidance-inlined-in-every-repo.md)).

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

The hook writes the same block to `~/.codex/AGENTS.md` whenever `~/.codex`
exists — Codex's global **user** instructions, outside its 32 KiB
`project_doc_max_bytes` project-doc budget. Register it once per machine with
`scripts/register-codex-hook.sh` from an `_agent-guidance` checkout, then
trust it in `/hooks`. `codex debug prompt-input` renders instructions loaded
from disk by its own diagnostic process; it does not run SessionStart hooks
or inspect an existing session or daemon. Verify delivery in the launched
session's initial instructions and verdict; see the
[2026-10-04 evidence](https://github.com/Adam-S-Daniel/_agent-guidance/blob/main/docs/evidence/codex-trust-and-daemon-0160.md#prompt-debugging-source-read-2026-10-04-cli-01600-rust-v01600).

For Codex Cloud, use **Manual** environment setup with persistent
`CODEX_HOME=/opt/codex`. Preserve the repository's dependency setup and run
`bash .claude/hooks/fleet-memory.sh --codex-cloud` in both setup and
maintenance; reset the cache for the first verification. Fresh setup and
cached maintenance were verified in the `_agent-guidance` environment. See
[`docs/codex-cloud.md`](https://github.com/Adam-S-Daniel/_agent-guidance/blob/main/docs/codex-cloud.md).
If the Cloud shell has no `codex debug prompt-input`, the saved task response's
raw initial instruction envelope is the echo-free proof of model-visible
delivery.

## The floor: rules that hold even when the guidance did not load

These are the ones with teeth. They are restated here, deliberately, because a
session that lost the guidance must not also lose these.

- **Branch protection is real.** Fleet repos are PR-only on their default
  branch; a direct push is rejected (GH013), even from the repo's own
  workflows. Never design a bot that pushes to a protected default branch.
- **Every `uses:` is pinned to a full 40-character commit SHA, with no
  trailing version comment.** The one carve-out is a ref into this account's
  own `cms-platform`, which stays on its release tag.
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
- **The fleet spans TWO owners** — `Adam-S-Daniel` and `jodidaniel`. A query
  scoped to one returns a plausible, complete-shaped, wrong answer.
- **Anything you name gets its link** — what you hand over, what you are
  waiting on, and what you cite as already done.
- **Merge with a merge commit** (`gh pr merge --merge`); do not amend
  published commits or force-push shared branches.
- **Keep this file under 32 KiB.** Codex truncates project instructions at
  that byte silently; the sync warns and the drift report flags
  `codex-truncated`.

<!-- END MANAGED SECTION -->
## Repo-specific additions

**`AGENTS.md` in this repo is a generated artifact.** Everything above the marker
is `scripts/build-agents-md.sh` output — edit `agents-md/base.md` (or a file under
`agents-md/sections/`), never this file's managed half. CI regenerates and diffs
it, so a base.md edit without a regenerated `AGENTS.md` fails the build.

Why it is committed here at all, when `sync.sh` writes it everywhere else: the
sync excludes its own repo (`SYNC_SELF_REPO`), so without this copy it would be
the one repo in the fleet whose agents never read the fleet's guidance. The
committed copy also means a PR that changes `base.md` shows the exact text every
consumer repo is about to receive, in the same diff, instead of deferring it to
an async run after merge.

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
[evidence](https://github.com/Adam-S-Daniel/_agent-guidance/blob/main/docs/evidence/2026-08-19-agents-md-doubled-managed-block.md).)

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
