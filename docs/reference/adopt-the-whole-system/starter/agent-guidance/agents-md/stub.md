# AGENTS.md

> **Managed by [`agent-guidance`].**
> Edit only below the `## Repo-specific additions` header.
> Everything above it will be overwritten on the next sync.

## Fleet guidance is delivered once per session — not by this file

The account's full guidance — fleet policy, machine layout, the traps that
cost real outages — is installed into **user memory** (`~/.claude/CLAUDE.md`)
by the `fleet-memory` SessionStart hook, so it is loaded **once per session**
no matter how many repos are attached. (Inlined into every repo instead, a
session with many repos attached loads one copy per repo and can spend a
third of its context window before the first prompt.)

**Check the session-start verdict before you rely on it.** The hook prints one
line:

- `fleet-guidance: installed (v<id>, <n> bytes)` or `fleet-guidance: current` —
  the full guidance is in context. Use it.
- `fleet-guidance: DEGRADED — <reason>` — it is **not** in context. You have
  only what is below. Read `agents-md/base.md` in the `agent-guidance`
  checkout (or on GitHub, `<you>/agent-guidance`) before non-trivial work,
  and say in your reply that you were running degraded.
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
- **Pin every third-party action, one style per repo.** Either a full
  40-character commit SHA on every `uses:` line (no trailing version
  comment), or an exact tag on every `uses:` line locked in
  `.github/workflows/actions.lock`, with `gh actions-lock --verify` run in
  CI. Never mix the two in one repo. Cross-repo reusable workflows take a
  full SHA either way.
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
- **A scoped search will not say what it missed.** Enumerate repos from the
  fleet's registry (`agent-guidance`'s `repos.yml`), not a search scoped to
  one owner or the local disk.
- **Anything you name gets its link** — what you hand over, what you are
  waiting on, and what you cite as already done.
- **Merge with a merge commit** (`gh pr merge --merge`); do not amend
  published commits or force-push shared branches.
