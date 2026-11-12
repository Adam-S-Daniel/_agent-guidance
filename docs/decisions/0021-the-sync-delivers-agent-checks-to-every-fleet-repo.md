# 0021 — The sync delivers the agent-checks runner, CI check and --no-verify guard to every fleet repo

**Status:** Accepted (2026-10-21)

## Context

adam-agentskills' ADR 0020 piloted commit-time checks for agents: one bash
runner, `.githooks/agent-checks`, run by Git 2.54 config-based hooks on owner
machines and cloud sessions, and by a CI job that is the real enforcement. The
pilot merged and behaved on the owner's Windows and WSL machines and in all four
cloud environments. Two things were left for later: a guard against the easy
skip (`--no-verify`), and the same checks in every other repo.

The machine-level install already covers every repo of both owners: the global
`includeIf "hasconfig:remote.*.url:..."` include runs `.githooks/agent-checks`
in any clone whose remote is Adam-S-Daniel or jodidaniel, and does nothing
where that file is absent. So what each repo lacks is only the runner and the
CI check. This repo's sync already reaches every repo of both owners, and
already ships managed files (`fleet-memory.sh`), an add-only workflow (the
Dependabot auto-merge one) and a `.gitattributes` line (ADR 0019).

## Decision

A fifth sync lane, on when `repos.yml` has `agent_checks: deliver: true`, takes
its files from `templates/agent-checks/`:

1. **The runner** `.githooks/agent-checks` is **managed**: overwritten whenever
   it differs from the template, so a check added here reaches every repo on
   the next sync. This repo now holds the canonical copy; adam-agentskills'
   pilot copy is overwritten like any other.
2. **The CI workflow** `.github/workflows/agent-checks.yml` is **added once**
   and never overwritten, so Dependabot can keep its pinned checkout current.
   It is not added where any workflow already runs `.githooks/agent-checks`
   (adam-agentskills runs it as a job in `ci.yml`), where `actions.lock` pins
   the repo's actions as tags, in a cms-platform consumer (a root
   `platform.lock`), or where the path is gitignored.
3. **The guard** `.claude/hooks/agent-checks-guard.sh` is a Claude Code
   PreToolUse hook (matcher `Bash|Edit|Write|MultiEdit|NotebookEdit`), managed
   like the runner and registered through the same registrar as the other
   hooks. It refuses `--no-verify` and `git commit -n`, hook config overridden
   with `-c`, `--config-env` or `GIT_CONFIG_*`, `git config` writes to
   `core.hooksPath`, `hook.*` or `includeIf.*`, and deleting or editing
   `.githooks/` or `agent-hooks.inc`. It is delivered wherever fleet-memory is
   (same refusals: `.claude/` gitignored, unparseable `settings.json`).
4. **A second `.gitattributes` line**, `.githooks/* text eol=lf`, written by
   the same helper as ADR 0019's line, because the runner has no `.sh` suffix
   and bash cannot parse it from a CRLF checkout.

## Consequences

- Every fleet repo gets the same checks, and a new check is one change here.
- The guard stops habit, not intent: a string check can be got around, and it
  fails open on any error of its own (no python3, input it cannot parse) so a
  broken guard never stops work. CI stays the enforcement, and it is only that
  once `agent-checks` is a required status check, which repo-settings declares.
- Codex and people at a terminal get no guard; for them the CI check is the
  only stop after a skipped hook.
- The workflow template pins `actions/checkout` by SHA, so repos that lock
  actions with `actions.lock` get the runner but must add the workflow by hand.
- A cms-platform consumer's `platform-pin-consistency` check fails on any
  workflow the platform does not dictate (measured on adamdaniel.ai and
  jodidaniel.com, 2026-10-21), so those sites get the runner and guard only.
  Their CI check has to come from the platform's own
  `examples/site/.github/workflows/`.
- The runner's pre-push check refuses a push to the default branch. This
  sync pushes to default branches directly, so a run on an owner machine with
  the hooks installed falls back to its PR path; CI runs have no hooks and are
  unaffected.

## Addendum (2026-10-26): the run log

Every `problem` call names a stable kebab-case check id (`rules-dir`,
`secret-file`, `skill-shell`, `staged-index`, `push-to-default`, `repo-local`, a lesson's
`lesson-<id>`, ...). Each local pre-commit and pre-push run appends one JSON
line, `{"v":1,"ts","event","repo","checks","failed"}`, to `$AGENT_CHECKS_LOG`
(default `${XDG_STATE_HOME:-$HOME/.local/state}/agent-checks/runs.jsonl`), so
a dashboard can show how often each check runs and refuses per repo. `repo`
is origin's GitHub `owner/name` or null; the line holds no paths, messages,
branches or user names. CI and `tree` runs write nothing.
`AGENT_CHECKS_LOG=off` turns it off, and a failed write is silent. The
runner's header comment is the reference.

## Addendum (2026-10-26): the guard refuses persistent/* deletes

The guard also refuses deleting a remote `persistent/*` branch
(`git push <remote> --delete`/`-d`, a `:persistent/...` refspec, or
`gh api -X DELETE .../git/refs/heads/persistent/...`). Those branches outlive
their pull requests and hold data, and a private repo has no ruleset to stop
the delete.

## Addendum (2026-10-29): a warn-only pass, dated-phrasing

The runner gains its first check that never refuses, closing the last item
of [#348](https://github.com/Adam-S-Daniel/_agent-guidance/issues/348). After
the refusing checks, every event runs `.githooks/dated-phrasing.js` over the
same paths: it warns on dated history (an ISO date) and migration-relative
phrasing ("is now", "no longer", "moved to", ...) in the prose of every
`AGENTS.md` (its repo-specific half), `CLAUDE.md`, `SKILL.md` and
`agents-md/**`. That text loads into every session and is read as the rule
that holds now, so a story about what changed goes stale there; it belongs in
`docs/evidence/` or git ([ADR 0018](0018-incident-narratives-live-in-docs-evidence.md)).
It warns and never blocks, as [ADR 0017](0017-prompt-audit-runs-as-an-event-triggered-sweep.md)
requires of checks that grow out of the prompt audit: it never changes the
exit status, appears in the run log's `checks` only when it read a file, and
any failure of its own (no node or npm, an offline install) is one warning
line.

- **It parses Markdown with markdown-it**, because the fleet rule forbids a
  line scan for structure. The dependency is not vendored: a minified bundle
  in every repo's history is a gitleaks false-positive risk on repos whose
  scan reads full history. The sync delivers
  `.githooks/dated-phrasing/package.json` and `package-lock.json`, pinning
  markdown-it exactly with integrity hashes, and the runner installs them
  with `npm ci --ignore-scripts` into a cache keyed by the lockfile's sha256
  (`${XDG_CACHE_HOME:-$HOME/.cache}/agent-checks/dated-phrasing/`, under
  `$RUNNER_TEMP` in CI), only when a path in scope changed.
- **The script, the fleet allowlist and the two package files are managed**
  like the runner (`AC_DATED_FILES` in `scripts/sync.sh`), and this repo keeps
  byte-identical self-copies under `.githooks/`, which the suite compares.
- **Text that is right as written is allowlisted, not reworded.** The fleet
  list is `templates/agent-checks/dated-phrasing-allowlist.txt`, reviewed
  against every fleet repo's default branch; a repo adds its own lines in
  `.agent-checks/dated-phrasing-allowlist.txt`, which the sync never touches.
  An entry, `<owner/repo> <path> :: <text>`, silences the Markdown block
  whose source holds `<text>`.

## Addendum (2026-11-02): pr-tldr, and one edit to the added-once workflow

The owner asked that every pull request description begin with a
plain-English TLDR. The runner's `ci` event gains a `pr-tldr` check for it,
because this workflow is already a required check in every fleet repo, so
the rule reaches the fleet with no ruleset change.

- **The test is lexical, never a model.** In a `pull_request` or
  `pull_request_target` run the runner reads the description from the event
  payload (`$GITHUB_EVENT_PATH`) with node: after HTML comments, blank lines
  and a `_Requested by ..._` attribution line, the first line must be an ATX
  heading reading TLDR or TL;DR, with text under it before the next heading.
  The same description always gets the same answer, and the refusal says
  what is wrong and how to fix it. It never prints the description.
- **Bots are warned, not refused.** A pull request whose author's type is
  `Bot` (Dependabot, a GitHub App, github-actions) gets a warning line, so
  automation that cannot write a TLDR is not blocked. The runner decides this
  in one place, so it can be tightened later.
- **Pull requests opened before the cutoff are exempt**
  (`pr_tldr_cutoff`, 2026-11-03T00:00:00Z), so a pull request already open
  when the rule ships is not failed retroactively.
- **The sync makes one narrow edit to the workflow.** Item 2 above adds the
  workflow once and never overwrites it. A re-run reads the payload of the
  event that started the run, so only a new `edited` event sees a corrected
  description, and a repo whose workflow has a bare `pull_request:` trigger
  would stay red after the fix. The template lists
  `types: [opened, edited, reopened, synchronize]`, and for an existing
  `.github/workflows/agent-checks.yml` that runs the runner the sync adds
  `edited` to that trigger: a `types` line under a bare `pull_request:`, or
  the word added to a one-line `types: [...]` list. It reads the shape with
  yq, edits one line, and keeps the edit only when yq reads the same
  document with `edited` added; any other shape (an `on:` list, a block
  list) is left alone with a WARN naming the repo. A repo that runs the
  runner from another workflow (adam-agentskills' `ci.yml`) is not edited.
- **This does not conflict with Dependabot keeping the pin current.** The
  reason for item 2 is that Dependabot rewrites the workflow's `uses:` line,
  and an overwrite from the template would undo its bump. This edit never
  touches `uses:` or anything outside the `pull_request` trigger's `types`,
  so Dependabot's pin and the sync's trigger edit change different lines, and
  once `edited` is listed the sync leaves the file alone again.

## Addendum (2026-11-12): hosted sessions turn the git hooks on themselves

The runner is delivered to every repo, but a cloud session never ran it as a git hook. Config-based hooks need git 2.54, which cloud containers have, and adam-agentskills' `scripts/install-agent-hooks.sh --system` wires them, but the cloud setup script runs that installer only when an attached repo ships it, and most projects ship none. So no cloud commit or push was checked or logged, and the explorer's hook counts left cloud sessions out.

`fleet-memory.sh` is the one user-scope SessionStart hook every cloud session already runs, and the sync already delivers it to every repo, so it now does the wiring. In a hosted session only (`CLAUDE_CODE_REMOTE=true` or `CLAUDE_CODE_REMOTE_SESSION_ID` set), with git 2.54 or newer, and when neither the system nor the global config already has an `includeIf` naming `agent-hooks.inc`, it writes `~/.config/git/agent-hooks.inc` with the installer's exact text and adds the installer's nine `includeIf` entries (three owners times three URL forms), to the system config when that file is writable and to the global config otherwise.

- **Same fail-safe as the installer.** Nothing points at an absolute script path. Git ignores a missing include file, and a repo without `.githooks/agent-checks` runs nothing, so no state this step leaves behind can break a commit or push by itself.
- **A refusal is the point.** In a repo that has the runner, a pre-commit or pre-push check that refuses now stops the commit or push in a cloud session, as it does on a laptop. Fix the cause; do not use `--no-verify`.
- **It never fails the session start.** The step is a function run in a subshell with its output discarded; any error skips it, and the verdict line and exit code are unchanged. It is silent on success and idempotent.
- **No sibling script.** The sync and the plugin copies deliver `fleet-memory.sh` alone, so a helper beside it would not arrive. The step is a function in the file.
- **Opt-out.** `FLEET_GIT_HOOKS=off` skips it. The test suite sets it, so a cloud container running the suite does not edit its own git config.
- **The include text is copied, not read.** adam-agentskills is not available in CI, so `test_fleet_memory_git_hooks` embeds the expected text with a comment naming its source. If the installer's output changes, change both.
- **Their runs are uploaded.** With the hooks running, the cloud runs reach the private usage store as phase 3 of `guidance-load-record.sh` (ADR 0036, ADR 0037 addenda).
