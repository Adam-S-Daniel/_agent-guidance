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

## Working in these repos

- **An issue or PR labeled `on-hold` is paused by the owner.** Do not work on
  it (no commits, reviews, merges, closing or follow-ups that do it); read,
  cite and list it as on hold without asking again. A held PR stays open.
  Only the owner adds or removes the label, or an agent the owner directs in
  that session.

## Data exposure in CI and public repos

CI logs, summaries, artifacts, run pages and git history are public on a
public repo.

- **Never print personal or sensitive data to a log** — emails, contacts,
  names, IDs, mailbox counts, tokens. Deliver results out-of-band (email the
  account itself); log a non-identifying status.
- **Don't interpolate `${{ inputs.* }}` / `${{ github.event.* }}` into a
  `run:` block** — the rendered command is echoed. Read inputs from
  `$GITHUB_EVENT_PATH`; `::add-mask::` sensitive values first.
- **Sensitive config goes in secrets**, not inputs or `vars`.
- **Sanitize error output** — never dump an API/HTTP body; log a status code
  plus error type, the data-bearing call inside the try/catch.
- **Least privilege:** minimal `permissions:` (usually `contents: read`);
  require approval for outside-collaborator fork PRs.
- **Fixtures use `example.com` / `example.net` only.**
- **Sanitize before the first commit.** Committed sensitive data means
  rewriting history, deleting every ref to it (branches, tags, PRs) and
  force-pushing — the one sanctioned force-push.
- **Commit with the `…@users.noreply.github.com` identity** on public repos.

## Automation vs branch protection

Fleet repos are PR-only on the default branch via ruleset, managed in
`repo-settings` (ADR 0001).

- Never design a bot that pushes to a protected default branch — rejected
  (GH013), even from its own workflows.
- Generated data goes on an unprotected results branch (skills-evals'
  `persistent/eval-results`), treated as untrusted.
- **A branch that outlives its PRs is `persistent/<purpose>`** (results,
  routine accumulation), guarded by a per-repo deletion-only
  `extra_rulesets` entry on `persistent/**` in `fleet.yml` (repo-settings
  ADR 0007). Never delete one; stale-branch cleanups skip the prefix.
- A bot that must write to a default branch needs a ruleset bypass actor
  declared in repo-settings' `fleet.yml`, never
  a hand-granted UI bypass.
- PR + auto-merge is a sanctioned bot-write path only where a repo-settings
  ADR declares the exception: the eval and guidance loops' PRs (ADRs 0003,
  0012, 0014), lesson-check PRs (0013) and the changelog routine's (0015);
  example-site-platform-managed repos use it by design.
- **A required status check gets no `concurrency` group** when its job can
  fire twice on one head sha (label events, `opened` + `synchronize`): a
  cancelled run can win the context and block the merge for good.
  `cancel-in-progress: false` does not help (only the latest pending run
  survives), a shared group cancels older siblings, so make triggers pairwise
  disjoint; `push`/`synchronize`-only jobs may keep `cancel-in-progress:
  true`. Lock it with a test that parses the YAML.
- **Every CI job on pull requests is a required check** unless the repo's
  `AGENTS.md` documents why not. Ship the job and its ruleset change in one
  PR (`PUT` checked-in ruleset JSON; read back what is live).
- **Every `pull_request`/`push` workflow filters on its salient paths**
  (`/acsi-coding-anywhere:workflow-path-audit`); update them when a step or
  dependency moves; table them in the repo's `AGENTS.md`.
  **A required check cannot use `on.paths`** — a missing check blocks the
  merge. Keep the trigger broad, detect salient changes in an early step,
  gate every later step on its output: the job still reports success.

## Agent-made GitHub contributions carry a marker

They post as the owner
([ADR 0023](https://github.com/example-adopter/_agent-guidance/blob/main/docs/decisions/0023-mark-agent-contributions.md)),
so each PR or issue body, comment, review or reply ends with
`<sub>🤖 Written by <agent> on behalf of @example-adopter</sub>`
(`Claude Code`/`Codex`); a PR or issue you open gets `agent-authored`;
commits keep `Co-Authored-By:` and add `Agent: claude-code`/`codex`.
Explain a merge, close or label change in a marked comment.
**Never submit an APPROVE review**: it cannot be marked. The
`github-write-marker.sh` hook enforces it.

## "The watch finished" is not "CI passed"

Never read pass/fail off a watch command's exit code: in `cmd | tail` `$?` is
`tail`'s, a backgrounded watch reports that pipeline code, and `tail -N`
hides FAILURE lines.

- Capture the real code with `${PIPESTATUS[0]}`, or don't pipe the watch.
- After any CI watch, query the conclusions and report the parsed result:

  ```bash
  gh pr view <n> --repo <owner>/<repo> --json statusCheckRollup --jq \
    '.statusCheckRollup[] | (.conclusion // .state) as $c
     | select($c != null and $c != "SUCCESS" and $c != "NEUTRAL")
     | "\(.name // .context): \($c)"'
  ```

  A check run carries `.conclusion`, a legacy status `.state`; filter on one
  and the other reads clean. "Watch done" means "now verify".
- **A pipe into `grep -q` is a race.** `grep -q` exits at the first match, so
  past the 64 KiB pipe buffer the writer dies 141 and `pipefail` reads a
  present marker as absent (issue #81; 20 trials per size: 48 kB 0/20,
  95 kB 18/20). Feed data as an argument or here-string —
  `grep -qxF -- "$m" <<<"$s"`.
  A size-dependent bug needs trials, not a probe; say how many.
  `$( ... || true )` is safe by accident — say so where you find it.
- **`gh api ... --jq` on an HTTP error prints the raw error body to stdout**,
  so `|| true` captures it. Discard explicitly:
  `out=$(gh api ... --jq '.foo') || out=""`.

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

## Pinning GitHub Actions

**Two forms while repos migrate** ([ADR 0024](https://github.com/example-adopter/_agent-guidance/blob/main/docs/decisions/0024-actions-pin-through-lockfiles.md)):

- **Lockfile, the target:** `.github/workflows/actions.lock` covers every
  workflow; each `uses:` on its most exact tag, by hand; local actions
  `$/<path>`. Then `gh actions-lock --no-narrow`. Repos where actionlint
  meets `$/`, or with `./` refs into a runtime checkout (`example-site-platform`),
  stay legacy.
- **Legacy:** full 40-character SHA, no trailing version comment.
- **Cross-repo reusable workflows take a full SHA in both forms**; no lockfile
  covers them.
- **The runner runs whatever commit the lockfile names**, even off-tag:
  require `Adam-S-Daniel/verify-actions-lock` (fails on any pin finding) and
  the Require lockfile policy. Review `actions.lock` as pins.
- **Wait 7 days after a third-party release**, else pin the previous.
- **Dereference annotated tags:** `git ls-remote <url> 'refs/tags/<tag>^{}'`.
- **The one carve-out:** `example-site-platform` refs stay on its release tag.

`sha_pinning_required: true` stays on; a locked tag satisfies it.

## Skills ecosystem

- The registry is `example-adopter/acsi-agentskills`: plugins
  `acsi-anything-anywhere`, `acsi-coding-anywhere`,
  `acsi-coding-local`, `acsi-non-coding-local`; invoke
  `/<plugin>:<skill>`; `setup.sh` sets up a machine.
- **A `git push` failing in EVERY repo**: retired sync-skills hook targets a
  deleted script; `setup.sh --owner-machine` removes it.
- Cloud/ephemeral sessions get no plugins from repo-declared settings
  (registry ADR 0001): the repo's `skills.lock` and `skills-bootstrap`
  SessionStart hook install the bundles, digest-verified; read the
  `skills:` verdict at start.
- **Adoption is opt-in and double-keyed:** an entry in `_agent-guidance`'s
  `repos.yml` AND a `skills.lock` the repo committed itself (the sync never
  writes one). Bundles cost always-on context, so a repo may be deliberately
  out — check for `skills.lock`, don't guess.
- **Terminals load the claude.ai account's skills** as `/<name>` (or
  `/anthropic-skills:<name>` if taken); `setup.sh` opts a machine out
  (`syncClaudeAiSkills: false`), cloud can't (registry ADR 0010).
- New reusable skills graduate into the registry (sensitive ones in
  `acsi-agentskills-private`); a long skill splits across files.

## Two setup gaps you may close, and must not nag about

No repo can commit either; both are silent when missing. **Detect first;
say nothing when the check passes.** Once per session, not as a greeting, not only for skills work.

**Cloud (claude.ai) — PROMPT, never act.** Resolve `$project` first:
`$CLAUDE_PROJECT_DIR` when set (**unset** in `remote_mobile`), else the
nearest ancestor of the cwd holding more than one repo checkout; an empty
value probes `/` and silently suppresses the prompt.
Prompt only when all four hold:

1. hosted — entrypoint `remote*`/`claude_in_slack`/`claude-in-slack`/
   `claude-in-teams`, **or** `$CLAUDE_CODE_REMOTE_SESSION_ID` is set;
2. multi-repo — `$project` has no `skills.lock` but a child does;
3. `$project/.claude/settings.json` does not already register a
   `skills-bootstrap` SessionStart hook;
4. some child ships `.claude/hooks/skills-bootstrap.sh`.

Then say it once, naming the snippet's home (acsi-agentskills'
`docs/multi-repo-delivery.md`; do not paraphrase it), and drop it.

**Durable machine — ACT, then one line.** `claude plugin marketplace list
--json` is `[]` when nothing is configured: absent → `claude plugin
marketplace add example-adopter/acsi-agentskills`, install the plugins wanted;
marketplace behind → `claude plugin marketplace update acsi-agentskills`;
install behind → below; both current → silence. Use CLI ≥ 2.1.280 (older
updates kept stale SHAs). After refreshing the clone (`installLocation`),
compare each `gitCommitSha` in `~/.claude/plugins/installed_plugins.json` with
`git -C <clone> rev-parse HEAD`: equal is current, different needs inspection,
missing is unknown. The bundle is at `installPath`, which a marketplace
refresh does not update; the clone is `--depth 1`, so ancestry checks may
exit 128. Try `claude plugin update` and recheck; still stale →
uninstall/reinstall (registry ADR 0009). Updates load next session. Neither
belongs in a repo's `AGENTS.md`.

## Git practices

- **Merge with a merge commit — `gh pr merge --merge`.** Squash and rebase
  are off (`--squash` fails; do not offer it). Squash strands commits a
  lockfile pins by sha (2026-08-15: `cannot resolve ref`).
