# AGENTS.md

> **Managed by [`_agent-guidance`].**
> Edit only below the `## Repo-specific additions` header.
> Everything above it will be overwritten on the next sync.

Only what is **specific to this account and learned the hard way**; depth
lives in each repo's `docs/` and the skills registry. Delivered once per
session to BOTH `~/.claude/CLAUDE.md` and `~/.codex/AGENTS.md`, so a rule for
every agent goes here, not in `~/.claude/AGENTS.md` (one tool only). Keep it
under 25 KiB; Codex silently cuts a full-mode AGENTS.md at 32 KiB.

<!-- TEMPLATE: copy the block below outside this comment to add a section.

    ## A short title naming the situation, in the words an agent would search for

    One line of why: the incident or pattern behind the rules, with a date.

    - **The rule, in bold.** One sentence on what to do instead.
    - **A second rule.** Keep each rule checkable by an eval fixture.

Then, in the same pull request, add a row for the heading to
`agents-md/eval-coverage.yml` (status `gap` until a skills-evals fixture
exists, then `covered`), add an entry to `docs/guidance-impact.md`, and run
`node scripts/check-guidance-coverage.js --write-bytes`.
-->

## A successful `git push` does not mean your commit exists

A pre-commit hook that refuses the commit does not stop the push: `git push`
pushes the base commit and prints an ordinary success. The
fleet's `secrets-scan` guard (example-site-platform's `dev-hooks-sync.yml`) FAILS
CLOSED without `gitleaks` on `PATH`, which a fresh container lacks
(2026-08-25, example-site-a).

- **Verify the commit, not the push:** `git log --oneline -1` shows your
  message and `git status --short` is clean, or `git rev-parse HEAD` differs
  from `git rev-parse origin/<base>`.
- **`&&`-chain commit into push**; a newline or `;` lets a refused commit
  become a pushed branch.
- **Install the tool; skip the bypass.** `SKIP_SECRETS_SCAN=1` is for
  emergencies; the binary is one `curl` away.
- **A push can carry the WRONG ref while both checks pass.**
  `git push -u origin <name>` pushes the LOCAL branch of that name, maybe
  not the one you are on (2026-08-29: the commit landed on `main` and
  the push updated a stale local branch). The only check naming commit AND
  remote branch: `git merge-base --is-ancestor <sha> origin/<branch>`, after
  every push.
