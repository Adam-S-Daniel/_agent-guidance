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
loaded **once per session** no matter how many repos are attached. It used to
be inlined here in every repo, which meant a session with 19 repos open
carried 19 identical copies: 332.3k tokens of a 1M window, measured
2026-08-29.

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

<!-- END MANAGED SECTION -->
## Repo-specific additions

**`AGENTS.md` in this repo is a generated artifact.** Everything above the marker
is `scripts/build-agents-md.sh` output — edit `agents-md/base.md` (or a file under
`agents-md/sections/`), never this file's managed half. CI regenerates and diffs
it, so a base.md edit without a regenerated `AGENTS.md` fails the build.

Why it is committed here at all, when `sync.sh` writes it everywhere else: the
sync excludes its own repo (`SYNC_SELF_REPO`), so for as long as this repo has
existed it was the one repo in the fleet whose agents never read the fleet's
guidance. Committing the rendered output fixes that and buys a second thing —
a PR that changes `base.md` shows the exact text ~20 repos are about to receive,
in the same diff, instead of deferring it to an async run after merge.

Regenerate with:

```bash
printf '%s\n%s\n' "$(./scripts/build-agents-md.sh)" \
  "$(sed -n '/^## Repo-specific additions/,$p' AGENTS.md)" > AGENTS.md.new \
  && mv AGENTS.md.new AGENTS.md
```

The recipe above is line-anchored, but between commit `c86465f` and this fix
some tooling split the file on the first OCCURRENCE of the marker substring
instead — and the managed block's own BEGIN header quotes the marker verbatim
(`DO NOT EDIT ABOVE "## Repo-specific additions"`), so that split anchored on
the header line rather than the real heading and treated the entire prior
managed block as repo-specific content to preserve. Every regen after that
prepended a fresh managed block on top of the old one, so the file carried two
managed blocks — including two contradictory copies of the skills-ecosystem
rule — for four commits (through `7b87581`). Because the recipe's own anchor
kept matching the same corrupted line, the doubled file was a fixed point of
regeneration, so the staleness check above stayed green throughout; only a
check that counts markers and asserts their order can tell a doubled file from
a well-formed one, which is what `scripts/check-agents-md.sh` does, and CI now
runs it ahead of the staleness check for exactly this reason.
